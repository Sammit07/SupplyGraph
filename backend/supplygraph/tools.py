"""Graph tools: the only way the agent touches data.

Design rules (after "How to build an agent that reasons over a graph"):

* The model never writes Cypher. Each tool runs fixed queries written here; model input
  arrives as validated, bounded Pydantic models and is passed as query parameters.
  The only formatted values are traversal bounds, which are validated integers.
* Names are resolved against the graph before any traversal. An unresolved name returns
  ``unknown_entity`` and nothing else runs. Several matches return ``ambiguous`` with
  candidates.
* "Nothing found" is reported as ``not_in_graph``, never as "no impact".
* Traversals run read-only. Only ``record_finding`` writes, and only Finding nodes.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .db import GraphStore, Rows
from .models import EntityRef, Outcome, ToolResult

EntityKind = Literal["Supplier", "Part", "Product", "Customer", "Region", "Port", "Disruption"]
MAX_DEPTH = 10
DEFAULT_DEPTH = 8
FULL_LOSS = 0.999  # share_lost at or above this means no unaffected supplier remains
HIT_REASONS = {"direct", "site_in_affected_region", "all_export_ports_blocked"}


class ToolInputError(ValueError):
    """The call was well-formed but cannot be answered as asked (shown to the model)."""


# --------------------------------------------------------------------------- inputs


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindEntityIn(_In):
    query: str = Field(min_length=1, max_length=200, description="Name, partial name or id.")
    kind: EntityKind | None = Field(default=None, description="Restrict to one entity kind.")


class DisruptionImpactIn(_In):
    entity: str = Field(
        min_length=1,
        max_length=200,
        description="Disruption, region, port, supplier or part (name or id) that is down.",
    )
    kind: EntityKind | None = Field(default=None, description="Kind of `entity`, if known.")
    duration_days: int | None = Field(
        default=None,
        ge=1,
        le=730,
        description="How long the outage lasts. Defaults to the disruption's expected end.",
    )
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH, description="BOM hops.")


class CombinedImpactIn(_In):
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH, description="BOM hops.")


class UpstreamExposureIn(_In):
    entity: str = Field(min_length=1, max_length=200, description="Product or customer.")
    kind: Literal["Product", "Customer"] | None = None
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH)


class SinglePointsIn(_In):
    product: str | None = Field(
        default=None, max_length=200, description="Limit to one product; omit for all."
    )
    limit: int = Field(default=15, ge=1, le=50)
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH)


class AlternateSuppliersIn(_In):
    part: str = Field(min_length=1, max_length=200, description="Part name or id.")


class TraceConnectionIn(_In):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    max_hops: int = Field(default=10, ge=1, le=12)


class CriticalLeadTimeIn(_In):
    product: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=3, ge=1, le=10)
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH)


class SupplierCriticalityIn(_In):
    limit: int = Field(default=10, ge=1, le=50)
    max_depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH)


class ActiveDisruptionsIn(_In):
    include_inactive: bool = False


class RecallFindingsIn(_In):
    entity: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=20)


class SearchDocumentsIn(_In):
    query: str = Field(
        min_length=2, max_length=300, description="What to look for in ingested documents."
    )
    entity: str | None = Field(
        default=None,
        max_length=200,
        description="Only passages about this supplier, port or region (name or id).",
    )
    limit: int = Field(default=5, ge=1, le=10)


class RecordFindingIn(_In):
    entity: str = Field(min_length=1, max_length=200, description="Entity the finding is about.")
    text: str = Field(
        min_length=10,
        max_length=800,
        description="One self-contained, evidence-backed statement worth keeping.",
    )


# --------------------------------------------------------------------------- queries

Q_RESOLVE_ID = """
MATCH (n:Entity {id: $id})
RETURN n.id AS id, n.name AS name, n.kind AS kind
"""

Q_RESOLVE_FULLTEXT = """
CALL db.idx.fulltext.queryNodes('Entity', $q) YIELD node, score
WITH node, score
WHERE $kinds IS NULL OR node.kind IN $kinds
RETURN node.id AS id, node.name AS name, node.kind AS kind, score
ORDER BY score DESC
LIMIT 10
"""

Q_RESOLVE_CONTAINS = """
MATCH (n:Entity)
WHERE ($kinds IS NULL OR n.kind IN $kinds) AND toLower(n.name) CONTAINS $needle
RETURN n.id AS id, n.name AS name, n.kind AS kind
LIMIT 10
"""

Q_DISRUPTION = """
MATCH (d:Disruption {id: $id})
OPTIONAL MATCH (d)-[:AFFECTS]->(x:Entity)
RETURN d.id AS id, d.name AS name, d.category AS category, d.severity AS severity,
       d.started_at AS started_at, d.expected_end AS expected_end, d.active AS active,
       collect(CASE WHEN x IS NULL THEN NULL ELSE {id: x.id, name: x.name, kind: x.kind} END)
         AS affects
"""

# A supplier is hit when it is itself affected, sits in an affected region, or every port
# it exports through is affected (directly or via the port's region). Losing only some of
# its ports degrades it but does not count as a hit.
Q_SUPPLIERS_HIT = """
MATCH (s:Supplier)
OPTIONAL MATCH (s)-[:LOCATED_IN]->(r:Region)
OPTIONAL MATCH (s)-[:SHIPS_VIA]->(p:Port)
OPTIONAL MATCH (p)-[:LOCATED_IN]->(pr:Region)
WITH s, r,
     collect(DISTINCT p.id) AS ports,
     collect(DISTINCT CASE WHEN p.id IN $anchors OR pr.id IN $anchors THEN p.id END) AS blocked,
     collect(DISTINCT CASE WHEN p.id IN $anchors THEN p.id
                           WHEN pr.id IN $anchors THEN pr.id END) AS via
WITH s, r, ports, blocked, via,
     CASE WHEN s.id IN $anchors THEN 'direct'
          WHEN r.id IN $anchors THEN 'site_in_affected_region'
          WHEN size(ports) > 0 AND size(blocked) = size(ports) THEN 'all_export_ports_blocked'
          WHEN size(blocked) > 0 THEN 'some_export_ports_blocked'
          ELSE NULL END AS reason
WHERE reason IS NOT NULL
RETURN s.id AS id, s.name AS name, r.id AS region_id, r.name AS region, reason,
       ports, blocked, via
"""

Q_PARTS_HIT = """
MATCH (s:Supplier)-[sup:SUPPLIES]->(part:Part)
WHERE s.id IN $hit
WITH part, collect(DISTINCT s.id) AS hit_ids, sum(sup.share) AS share_lost
OPTIONAL MATCH (alt:Supplier)-[asup:SUPPLIES]->(part)
WHERE NOT alt.id IN $hit
WITH part, hit_ids, share_lost,
     collect(CASE WHEN alt IS NULL THEN NULL
                  ELSE {id: alt.id, name: alt.name, share: asup.share,
                        lead_time_days: asup.lead_time_days} END) AS alternates
RETURN part.id AS id, part.name AS name, part.inventory_days AS inventory_days,
       hit_ids, share_lost, alternates
"""

Q_PARTS_BY_ID = """
MATCH (part:Part) WHERE part.id IN $ids
RETURN part.id AS id, part.name AS name, part.inventory_days AS inventory_days
"""


def q_propagate(depth: int) -> str:
    return f"""
UNWIND $parts AS pid
MATCH (part:Entity {{id: pid}})
MATCH path = (part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Product)
RETURN pid AS part_id, prod.id AS product_id, prod.name AS product_name,
       prod.annual_revenue AS revenue,
       [n IN nodes(path) | {{id: n.id, name: n.name, kind: n.kind}}] AS chain,
       reduce(d = 0, n IN nodes(path) | d + coalesce(n.inventory_days, 0)) AS buffer_days
"""


Q_CUSTOMERS_OF = """
MATCH (prod:Product)-[so:SOLD_TO]->(c:Customer)
WHERE prod.id IN $products
RETURN prod.id AS product_id, c.id AS id, c.name AS name, so.annual_revenue AS revenue
"""

Q_PRODUCTS_OF_CUSTOMER = """
MATCH (prod:Product)-[:SOLD_TO]->(c:Entity {id: $id})
RETURN prod.id AS id, prod.name AS name
"""


def q_upstream(depth: int) -> str:
    return f"""
MATCH path = (part:Part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Product)
WHERE prod.id IN $products
WITH part, min(length(path)) AS tier
MATCH (s:Supplier)-[sup:SUPPLIES]->(part)
OPTIONAL MATCH (s)-[:LOCATED_IN]->(r:Region)
OPTIONAL MATCH (s)-[:SHIPS_VIA]->(p:Port)
RETURN s.id AS supplier_id, s.name AS supplier, part.id AS part_id, part.name AS part,
       tier, sup.share AS share, r.id AS region_id, r.name AS region, r.country AS country,
       collect(DISTINCT p.id) AS ports
"""


Q_ACTIVE_TARGETS = """
MATCH (d:Disruption)-[:AFFECTS]->(x:Entity)
WHERE d.active = true
RETURN d.id AS id, d.name AS name, x.id AS target_id
"""


def q_single_points(depth: int) -> str:
    return f"""
MATCH (part:Part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Product)
WHERE $product IS NULL OR prod.id = $product
WITH part, collect(DISTINCT prod) AS prods
MATCH (s:Supplier)-[:SUPPLIES]->(part)
OPTIONAL MATCH (s)-[:LOCATED_IN]->(r:Region)
WITH part, prods, collect(DISTINCT s) AS sups, collect(DISTINCT r.country) AS countries
WHERE size(sups) = 1 OR size(countries) = 1
RETURN part.id AS id, part.name AS name, part.inventory_days AS inventory_days,
       [x IN sups | {{id: x.id, name: x.name}}] AS suppliers, countries,
       [p IN prods | {{id: p.id, name: p.name, revenue: p.annual_revenue}}] AS products
"""


Q_SUPPLIERS_OF_PART = """
MATCH (s:Supplier)-[sup:SUPPLIES]->(part:Entity {id: $id})
OPTIONAL MATCH (s)-[:LOCATED_IN]->(r:Region)
RETURN s.id AS id, s.name AS name, r.name AS region, r.country AS country,
       sup.lead_time_days AS lead_time_days, sup.share AS share,
       sup.unit_cost AS unit_cost, s.risk_score AS risk_score
ORDER BY sup.share DESC
"""

TRACE_RELS = ["SUPPLIES", "COMPONENT_OF", "SOLD_TO", "LOCATED_IN", "SHIPS_VIA", "AFFECTS"]

# FalkorDB has no undirected shortestPath(); its SPpaths procedure walks both directions.
Q_TRACE = """
MATCH (a:Entity {id: $a}), (b:Entity {id: $b})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: $rels, relDirection: 'both',
                   maxLen: $hops, pathCount: 1})
YIELD path
RETURN [n IN nodes(path) | {id: n.id, name: n.name, kind: n.kind}] AS nodes,
       [r IN relationships(path) | {type: type(r), src: startNode(r).id, dst: endNode(r).id}]
         AS rels
"""


def q_bom_chains(depth: int) -> str:
    return f"""
MATCH path = (leaf:Part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Entity {{id: $product}})
RETURN [n IN nodes(path) WHERE n.kind = 'Part' | n.id] AS chain
"""


def q_bom_suppliers(depth: int) -> str:
    return f"""
MATCH (part:Part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Entity {{id: $product}})
WITH DISTINCT part
OPTIONAL MATCH (s:Supplier)-[sup:SUPPLIES]->(part)
RETURN part.id AS id, part.name AS name,
       collect(CASE WHEN s IS NULL THEN NULL
                    ELSE {{id: s.id, name: s.name, lead: sup.lead_time_days,
                          share: sup.share}} END) AS suppliers
"""


def q_criticality(depth: int) -> str:
    return f"""
MATCH (s:Supplier)-[sup:SUPPLIES]->(part:Part)
MATCH (part)-[:COMPONENT_OF*1..{_bound(depth)}]->(prod:Product)
WITH s, sup, part, collect(DISTINCT prod) AS prods
OPTIONAL MATCH (other:Supplier)-[:SUPPLIES]->(part)
WHERE other.id <> s.id
WITH s, sup, part, prods, count(DISTINCT other) AS others
RETURN s.id AS id, s.name AS name, part.id AS part_id, part.name AS part,
       sup.share AS share, others,
       [p IN prods | {{id: p.id, name: p.name, revenue: p.annual_revenue}}] AS products
"""


Q_DISRUPTIONS = """
MATCH (d:Disruption)
WHERE $all OR d.active = true
OPTIONAL MATCH (d)-[:AFFECTS]->(x:Entity)
RETURN d.id AS id, d.name AS name, d.category AS category, d.severity AS severity,
       d.started_at AS started_at, d.expected_end AS expected_end, d.active AS active,
       d.source AS source,
       collect(CASE WHEN x IS NULL THEN NULL ELSE {id: x.id, name: x.name, kind: x.kind} END)
         AS affects
ORDER BY d.severity DESC, d.started_at DESC
"""


def q_recall(limit: int) -> str:
    return f"""
MATCH (f:Finding)-[:ABOUT]->(e:Entity {{id: $id}})
RETURN f.id AS id, f.text AS text, f.created_at AS created_at, f.session_id AS session_id
ORDER BY f.created_at DESC
LIMIT {_bound(limit, 20)}
"""


Q_RECORD = """
MATCH (e:Entity {id: $id})
CREATE (f:Finding {id: $fid, text: $text, created_at: $ts, session_id: $sid, author: $author})
       -[:ABOUT]->(e)
RETURN f.id AS id
"""

Q_CHUNK_COUNT = "MATCH (c:Chunk) RETURN count(c) AS n"

# Nearest passages by vector, then out along the graph: the document that contains each
# passage, the disruptions that document reported (and what they affect), and the supply
# entities the passage mentions.
Q_SEARCH_CHUNKS = """
CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($vector)) YIELD node, score
MATCH (node)-[:PART_OF]->(doc:Document)
OPTIONAL MATCH (doc)-[:REPORTS]->(d:Disruption)
OPTIONAL MATCH (d)-[:AFFECTS]->(a:Entity)
OPTIONAL MATCH (m:Entity)-[:MENTIONED_IN]->(node)
RETURN node.id AS id, node.text AS text, node.page AS page, score AS distance,
       {id: doc.id, filename: doc.filename, issuer: doc.issuer, uploaded_at: doc.uploaded_at}
         AS document,
       collect(DISTINCT CASE WHEN d IS NULL THEN NULL
         ELSE {id: d.id, name: d.name, kind: 'Disruption', active: d.active} END) AS disruptions,
       collect(DISTINCT a.id) AS affected_ids,
       collect(DISTINCT CASE WHEN m IS NULL THEN NULL
         ELSE {id: m.id, name: m.name, kind: m.kind} END) AS mentions
ORDER BY distance ASC
"""

Q_NAMES = "MATCH (n:Entity) RETURN n.id AS id, n.name AS name, n.kind AS kind"


def _bound(value: int, ceiling: int = MAX_DEPTH) -> int:
    """Validate a traversal bound before it is formatted into a pattern."""
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= ceiling:
        raise ToolInputError(f"bound must be an integer in [1, {ceiling}]")
    return value


# --------------------------------------------------------------------------- helpers

_CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "CNY": "¥"}


def fmt_money(value: float | int | None, currency: str) -> str:
    """Compact money string, e.g. ``$2.71B`` or ``CHF 3.10M``."""
    v = float(value or 0)
    prefix = _CURRENCY_SYMBOL.get(currency.upper(), f"{currency.upper()} ")
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{prefix}{v / div:.2f}{suffix}"
    return f"{prefix}{v:,.0f}"


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# Legal forms and filler that documents add to names ("Nakdong Display Co., Ltd.").
_NOISE = {
    # legal forms and filler
    "co", "ltd", "inc", "llc", "gmbh", "corp", "plc", "sa", "ag", "bv", "bhd", "sdn",
    "the", "of", "and",
    # generic geographic words documents add ("Ayutthaya province", "Katanga region")
    "province", "region", "prefecture", "state", "oblast", "district", "county",
    "municipality", "area",
}  # fmt: skip


def normalize_name(text: str) -> str:
    """Comparable form of a name: lowercase alphanumeric tokens without noise words."""
    return " ".join(t for t in _TOKEN_RE.findall(text.lower()) if t not in _NOISE)


def names_overlap(text: str, name: str) -> bool:
    """True when every word of ``text`` (noise words aside) is a whole word of ``name``.

    Full-text search matches prefixes, so "New Port" finds "Port of Newcastle"; this tells
    a real partial match ("Ayutthaya province" -> "Ayutthaya") from a prefix accident.
    """
    words = set(normalize_name(text).split())
    return bool(words) and words <= set(normalize_name(name).split())


def fulltext_query(text: str, *, match_all: bool = True) -> str | None:
    """Build a safe RediSearch query from alphanumeric tokens only, prefix-matched.

    ``match_all`` ANDs the tokens; otherwise noise words are dropped and the rest are ORed.
    """
    tokens = [t for t in _TOKEN_RE.findall(text) if len(t) > 1][:8]
    if not match_all:
        tokens = [t for t in tokens if t.lower() not in _NOISE]
    if not tokens:
        return None
    terms = [f"{t}*" if len(t) >= 3 else t for t in tokens]
    return " ".join(terms) if match_all else "|".join(terms)


_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "from", "by", "with",
    "is", "are", "was", "were", "be", "been", "will", "would", "can", "could", "should", "do",
    "does", "did", "what", "when", "where", "which", "who", "how", "why", "it", "its", "this",
    "that", "these", "those", "our", "we", "they", "their", "any", "about", "there", "has", "have",
}  # fmt: skip


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def query_terms(text: str) -> list[str]:
    """Meaningful query words, lightly stemmed ("shipments" -> "shipment")."""
    words = [w for w in _TOKEN_RE.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS]
    return list(dict.fromkeys(_stem(w) for w in words))


def keyword_overlap(terms: list[str], passage: str) -> float:
    """Share of query terms that appear in the passage (as a word prefix), 0..1."""
    if not terms:
        return 0.0
    words = {_stem(w) for w in _TOKEN_RE.findall(passage.lower())}
    hits = sum(1 for t in terms if any(w.startswith(t) for w in words))
    return hits / len(terms)


def _ref(row: dict[str, Any], kind: str | None = None) -> EntityRef:
    return EntityRef(id=row["id"], name=row["name"], kind=kind or row.get("kind") or "Entity")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


# Graph time spent by the current tool call. Each tool call runs in its own task (and so
# its own context), so concurrent calls never mix their timings.
_QUERY_TIMINGS: ContextVar[list[float] | None] = ContextVar("query_timings", default=None)


def start_query_timer() -> list[float]:
    """Collect the elapsed time of every graph query run by this task from now on."""
    timings: list[float] = []
    _QUERY_TIMINGS.set(timings)
    return timings


@dataclass(slots=True)
class Resolution:
    entity: EntityRef | None
    candidates: list[EntityRef]
    # True only for a match by id or by (normalised) exact name. A sole partial match
    # ("Black Sea" -> "Black Sea Industrial Gases") resolves for agent tools but is not exact.
    exact: bool = False


# --------------------------------------------------------------------------- tools


class SupplyGraphTools:
    """Tool implementations bound to one tenant graph and one session."""

    def __init__(
        self,
        store: GraphStore,
        tenant: str,
        *,
        session_id: str | None = None,
        currency: str = "USD",
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
        embedder: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
        min_passage_similarity: float = 0.2,
    ) -> None:
        self.store = store
        self.tenant = tenant
        self.session_id = session_id or "anonymous"
        self.currency = currency
        self._today = today
        self._embed = embedder
        self._min_similarity = min_passage_similarity

    def _fmt(self, value: float | int | None) -> str:
        return fmt_money(value, self.currency)

    async def _read(self, cypher: str, params: dict[str, Any] | None, op: str) -> Rows:
        rows = await self.store.read(self.tenant, cypher, params, op=op)
        timings = _QUERY_TIMINGS.get()
        if timings is not None:
            timings.append(rows.elapsed_ms)
        return rows

    # ----------------------------------------------------------------- resolution

    async def resolve(self, text: str, kinds: list[str] | None = None) -> Resolution:
        text = text.strip()
        # Exact id first: an indexed lookup, and ids are what the UI and the model pass back.
        rows = await self._read(Q_RESOLVE_ID, {"id": text}, "resolve:id")
        hit = rows.first()
        if hit and (kinds is None or hit["kind"] in kinds):
            ref = _ref(hit)
            return Resolution(ref, [ref], exact=True)

        candidates: list[EntityRef] = []
        query = fulltext_query(text)
        if query:
            rows = await self._read(Q_RESOLVE_FULLTEXT, {"q": query, "kinds": kinds}, "resolve:ft")
            candidates = [_ref(r) for r in rows.rows]
        loose = fulltext_query(text, match_all=False)
        if not candidates and loose and loose != query:
            rows = await self._read(
                Q_RESOLVE_FULLTEXT, {"q": loose, "kinds": kinds}, "resolve:ft-any"
            )
            candidates = [_ref(r) for r in rows.rows]
        if not candidates:
            rows = await self._read(
                Q_RESOLVE_CONTAINS, {"needle": text.lower(), "kinds": kinds}, "resolve:contains"
            )
            candidates = [_ref(r) for r in rows.rows]

        wanted = normalize_name(text)
        same = [c for c in candidates if normalize_name(c.name) == wanted] if wanted else []
        if len(same) == 1:
            return Resolution(same[0], candidates, exact=True)
        if len(candidates) == 1:
            # Agent tools accept a sole partial match ("Nakdong" -> Nakdong Display); callers
            # that must not guess (document intake) check ``exact``.
            return Resolution(candidates[0], candidates, exact=False)
        return Resolution(None, candidates)

    def _unresolved(
        self, tool: str, text: str, res: Resolution, kinds: list[str] | None
    ) -> ToolResult:
        scope = f" among {', '.join(kinds)}" if kinds else ""
        if res.candidates:
            names = "; ".join(f"{c.name} [{c.kind} {c.id}]" for c in res.candidates[:8])
            return ToolResult(
                tool=tool,
                outcome=Outcome.AMBIGUOUS,
                summary=f"'{text}' matches several entities{scope}: {names}. "
                "Call again with the exact id.",
                candidates=res.candidates[:8],
                entities=res.candidates[:8],
            )
        return ToolResult(
            tool=tool,
            outcome=Outcome.UNKNOWN_ENTITY,
            summary=f"No entity named '{text}'{scope} exists in the graph. No query was run.",
        )

    async def find_entity(self, args: FindEntityIn) -> ToolResult:
        kinds = [args.kind] if args.kind else None
        res = await self.resolve(args.query, kinds)
        # A search tool shows every match, the exact one first, so the model can see that
        # "Kyushu" is a region *and* part of two supplier names.
        found = res.candidates
        if res.entity:
            found = [res.entity, *[c for c in res.candidates if c.id != res.entity.id]]
        if not found:
            return self._unresolved("find_entity", args.query, res, kinds)
        listing = "; ".join(f"{c.name} [{c.kind} {c.id}]" for c in found)
        return ToolResult(
            tool="find_entity",
            outcome=Outcome.ANSWERED,
            summary=f"Matches for '{args.query}': {listing}.",
            data={"matches": [c.model_dump() for c in found], "exact": res.entity is not None},
            entities=found,
            rows=len(found),
        )

    # ----------------------------------------------------------------- shared steps

    async def _active_targets(self) -> dict[str, list[dict[str, str]]]:
        """target entity id -> active disruptions that affect it."""
        rows = await self._read(Q_ACTIVE_TARGETS, None, "active_targets")
        out: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in rows.rows:
            out[r["target_id"]].append({"id": r["id"], "name": r["name"]})
        return out

    async def _suppliers_hit(self, anchors: list[str]) -> list[dict[str, Any]]:
        if not anchors:
            return []
        rows = await self._read(Q_SUPPLIERS_HIT, {"anchors": anchors}, "suppliers_hit")
        return rows.rows

    async def disrupted_supplier_ids(self) -> dict[str, str]:
        """Supplier id -> 'disrupted' | 'degraded' under all active disruptions."""
        active = await self._active_targets()
        return {
            r["id"]: "disrupted" if r["reason"] in HIT_REASONS else "degraded"
            for r in await self._suppliers_hit(sorted(active))
        }

    # ----------------------------------------------------------------- disruption impact

    async def disruption_impact(self, args: DisruptionImpactIn) -> ToolResult:
        tool = "disruption_impact"
        allowed = ["Disruption", "Region", "Port", "Supplier", "Part"]
        if args.kind and args.kind not in allowed:
            raise ToolInputError(
                "disruption_impact starts from a disruption, region, port, supplier or part. "
                "For products or customers use upstream_exposure."
            )
        kinds = [args.kind] if args.kind else allowed
        res = await self.resolve(args.entity, kinds)
        if res.entity is None:
            return self._unresolved(tool, args.entity, res, kinds)
        start = res.entity
        entities: dict[str, EntityRef] = {start.id: start}
        total_ms = 0.0

        duration = args.duration_days
        disruption_info: dict[str, Any] | None = None
        if start.kind == "Disruption":
            rows = await self._read(Q_DISRUPTION, {"id": start.id}, "disruption")
            total_ms += rows.elapsed_ms
            d = rows.first() or {}
            affects = [a for a in d.get("affects", []) if a]
            anchors = [a["id"] for a in affects]
            for a in affects:
                entities[a["id"]] = EntityRef(**a)
            end = _parse_date(d.get("expected_end"))
            if duration is None and end:
                duration = max((end - self._today()).days, 0)
            disruption_info = {
                "id": start.id,
                "name": start.name,
                "active": d.get("active"),
                "expected_end": d.get("expected_end"),
                "affects": affects,
            }
        else:
            anchors = [start.id]

        return await self._impact_from_anchors(
            tool, start, anchors, duration, args.max_depth, entities, disruption_info, total_ms
        )

    async def combined_impact(self, args: CombinedImpactIn) -> ToolResult:
        """All active disruptions at once. Losses compound: a part whose suppliers are hit by
        *different* disruptions can lose all its supply even if no single event blocks it."""
        active = await self._active_targets()
        if not active:
            return ToolResult(
                tool="combined_impact",
                outcome=Outcome.NOT_IN_GRAPH,
                summary="There are no active disruptions in the graph.",
            )
        names = sorted({d["name"] for ds in active.values() for d in ds})
        start = EntityRef(id="ALL-ACTIVE", name="All active disruptions", kind="Scenario")
        result = await self._impact_from_anchors(
            "combined_impact", start, sorted(active), None, args.max_depth, {}, None, 0.0
        )
        result.data["disruptions"] = names
        return result

    async def _impact_from_anchors(
        self,
        tool: str,
        start: EntityRef,
        anchors: list[str],
        duration: int | None,
        max_depth: int,
        entities: dict[str, EntityRef],
        disruption_info: dict[str, Any] | None,
        total_ms: float,
    ) -> ToolResult:
        """Propagate an outage of ``anchors`` through suppliers, parts, the BOM and sales."""
        # Step 1: which suppliers are knocked out, and why.
        hit_rows: list[dict[str, Any]] = []
        degraded_rows: list[dict[str, Any]] = []
        if start.kind != "Part":
            for r in await self._suppliers_hit(anchors):
                (hit_rows if r["reason"] in HIT_REASONS else degraded_rows).append(r)
        for r in hit_rows + degraded_rows:
            entities[r["id"]] = EntityRef(id=r["id"], name=r["name"], kind="Supplier")
        hit_ids = [r["id"] for r in hit_rows]
        supplier_name = {r["id"]: r["name"] for r in hit_rows}

        # Step 2: which parts lose supply, and what sourcing remains.
        if start.kind == "Part":
            rows = await self._read(Q_PARTS_BY_ID, {"ids": [start.id]}, "part")
            parts = [{**r, "hit_ids": [], "share_lost": 1.0, "alternates": []} for r in rows.rows]
        elif hit_ids:
            rows = await self._read(Q_PARTS_HIT, {"hit": hit_ids}, "parts_hit")
            total_ms += rows.elapsed_ms
            parts = rows.rows
        else:
            parts = []
        for p in parts:
            p["share_lost"] = round(min(float(p["share_lost"] or 0), 1.0), 3)
            p["alternates"] = [a for a in p["alternates"] if a]
            entities[p["id"]] = EntityRef(id=p["id"], name=p["name"], kind="Part")
            for a in p["alternates"]:
                entities[a["id"]] = EntityRef(id=a["id"], name=a["name"], kind="Supplier")

        if not parts:
            degraded = ", ".join(r["name"] for r in degraded_rows)
            summary = (
                f"{start.name} ({start.kind}) is in the graph, but no supplier is located in it, "
                "is it, or ships exclusively through it, so no supply path starts here."
            )
            if degraded:
                summary += f" Partially affected (some export ports blocked): {degraded}."
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=summary,
                data={
                    "start": start.model_dump(),
                    "disruption": disruption_info,
                    "degraded_suppliers": degraded_rows,
                },
                entities=list(entities.values()),
                query_ms=total_ms,
            )

        # Step 3: propagate each affected part up the bill of materials to products.
        part_by_id = {p["id"]: p for p in parts}
        rows = await self._read(q_propagate(max_depth), {"parts": list(part_by_id)}, "propagate")
        total_ms += rows.elapsed_ms
        # keep, per (part, product), the chain with the least buffer (first to run dry)
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows.rows:
            key = (r["part_id"], r["product_id"])
            if key not in best or r["buffer_days"] < best[key]["buffer_days"]:
                best[key] = r
        if not best:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"{start.name} affects {len(parts)} part(s), but none of them reaches a "
                f"finished product within {max_depth} bill-of-materials hops.",
                data={"start": start.model_dump(), "parts": [p["name"] for p in parts]},
                entities=list(entities.values()),
                query_ms=total_ms,
            )

        products: dict[str, dict[str, Any]] = {}
        for (part_id, product_id), r in best.items():
            part = part_by_id[part_id]
            blocked = part["share_lost"] >= FULL_LOSS
            prod = products.setdefault(
                product_id,
                {
                    "id": product_id,
                    "name": r["product_name"],
                    "annual_revenue": r["revenue"] or 0,
                    "status": "constrained",
                    "days_of_cover": None,
                    "affected_parts": [],
                    "critical_path": None,
                    "_rank": None,
                },
            )
            entities[product_id] = EntityRef(id=product_id, name=r["product_name"], kind="Product")
            for n in r["chain"]:
                entities[n["id"]] = EntityRef(**n)
            prod["affected_parts"].append(
                {
                    "part": part["name"],
                    "share_lost": part["share_lost"],
                    "blocked": blocked,
                    "buffer_days": r["buffer_days"],
                    "hops": len(r["chain"]) - 1,
                }
            )
            # Critical path: the blocked chain with the least cover, else the constrained one.
            rank = (0 if blocked else 1, r["buffer_days"])
            if prod["_rank"] is None or rank < prod["_rank"]:
                prod["_rank"] = rank
                prod["days_of_cover"] = r["buffer_days"]
                chain = [EntityRef(**n) for n in r["chain"]]
                if part["hit_ids"]:
                    sid = part["hit_ids"][0]
                    chain = [EntityRef(id=sid, name=supplier_name[sid], kind="Supplier"), *chain]
                prod["critical_path"] = chain
            if blocked:
                prod["status"] = "line_stop_risk"

        # Step 4: customers and revenue.
        cust_rows = await self._read(Q_CUSTOMERS_OF, {"products": list(products)}, "customers_of")
        total_ms += cust_rows.elapsed_ms
        customers: dict[str, dict[str, Any]] = {}
        for c in cust_rows.rows:
            entities[c["id"]] = EntityRef(id=c["id"], name=c["name"], kind="Customer")
            entry = customers.setdefault(
                c["id"],
                {"id": c["id"], "name": c["name"], "revenue_at_risk": 0.0, "revenue_exposed": 0.0},
            )
            entry["revenue_exposed"] += c["revenue"] or 0
            if products[c["product_id"]]["status"] == "line_stop_risk":
                entry["revenue_at_risk"] += c["revenue"] or 0

        paths: list[list[EntityRef]] = []
        product_list = []
        for prod in products.values():
            prod.pop("_rank", None)
            if prod["critical_path"]:
                paths.append(prod["critical_path"])
                prod["critical_path"] = " -> ".join(e.name for e in prod["critical_path"])
            prod["affected_parts"].sort(key=lambda x: (not x["blocked"], x["buffer_days"]))
            if duration is not None and prod["days_of_cover"] is not None:
                prod["stockout_expected"] = (
                    prod["status"] == "line_stop_risk" and duration > prod["days_of_cover"]
                )
            product_list.append(prod)
        product_list.sort(key=lambda p: (p["status"] != "line_stop_risk", -p["annual_revenue"]))

        at_risk = sum(p["annual_revenue"] for p in product_list if p["status"] == "line_stop_risk")
        exposed = sum(p["annual_revenue"] for p in product_list)
        parts_out = sorted(
            (
                {
                    "id": p["id"],
                    "name": p["name"],
                    "share_lost": p["share_lost"],
                    "inventory_days": p["inventory_days"],
                    "hit_suppliers": [supplier_name.get(s, s) for s in p["hit_ids"]],
                    "unaffected_alternates": p["alternates"],
                }
                for p in parts
            ),
            key=lambda x: -x["share_lost"],
        )[:25]

        stop = [p["name"] for p in product_list if p["status"] == "line_stop_risk"]
        constrained = [p["name"] for p in product_list if p["status"] != "line_stop_risk"]
        lead = (
            f"{start.name} knocks out {len(hit_rows)} supplier(s)"
            if start.kind != "Part"
            else f"Losing {start.name}"
        )
        summary = (
            f"{lead}, affects {len(parts)} part(s) and reaches {len(product_list)} product(s)."
        )
        if stop:
            summary += (
                f" Line-stop risk (a part with no unaffected supplier): {', '.join(stop)}; "
                f"revenue at risk {self._fmt(at_risk)}/yr."
            )
        if constrained:
            summary += f" Constrained but still sourced: {', '.join(constrained)}."
        if duration is not None:
            short = [p["name"] for p in product_list if p.get("stockout_expected")]
            summary += (
                f" Over a {duration}-day outage, inventory runs out for: {', '.join(short)}."
                if short
                else f" Inventory buffers cover a {duration}-day outage."
            )

        # Compounding: this event leaves alternates, but other active disruptions may already
        # have knocked those alternates out. Flag it so nobody reads "constrained" as "safe".
        compounded: list[str] = []
        if tool == "disruption_impact":
            status = await self.disrupted_supplier_ids()
            for p in parts:
                alts = p["alternates"]
                if (
                    p["share_lost"] < FULL_LOSS
                    and alts
                    and all(status.get(a["id"]) == "disrupted" for a in alts)
                ):
                    compounded.append(p["name"])
            if compounded:
                summary += (
                    f" WARNING: the remaining suppliers of {', '.join(compounded)} are knocked "
                    "out by other active disruptions, so combined these parts have no "
                    "unaffected supplier. Call combined_impact for the compounded effect."
                )

        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={
                "start": start.model_dump(),
                "disruption": disruption_info,
                "outage_days": duration,
                "currency": self.currency,
                "suppliers_hit": [
                    {"id": r["id"], "name": r["name"], "region": r["region"], "reason": r["reason"]}
                    for r in hit_rows
                ],
                "suppliers_degraded": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "blocked_ports": r["blocked"],
                        "open_ports": [p for p in r["ports"] if p not in r["blocked"]],
                    }
                    for r in degraded_rows
                ],
                "parts": parts_out,
                "products": product_list,
                "customers": sorted(customers.values(), key=lambda c: -c["revenue_at_risk"]),
                "revenue_at_risk": at_risk,
                "revenue_at_risk_display": self._fmt(at_risk),
                "revenue_exposed": exposed,
                "revenue_exposed_display": self._fmt(exposed),
                "compounded_parts": compounded,
                "notes": "days_of_cover sums inventory days held at each BOM stage on the "
                "thinnest chain; it is an estimate, not a schedule.",
            },
            entities=list(entities.values()),
            paths=paths,
            query_ms=total_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- upstream exposure

    async def upstream_exposure(self, args: UpstreamExposureIn) -> ToolResult:
        tool = "upstream_exposure"
        kinds = [args.kind] if args.kind else ["Product", "Customer"]
        res = await self.resolve(args.entity, kinds)
        if res.entity is None:
            return self._unresolved(tool, args.entity, res, kinds)
        start = res.entity
        entities: dict[str, EntityRef] = {start.id: start}
        if start.kind == "Customer":
            rows = await self._read(Q_PRODUCTS_OF_CUSTOMER, {"id": start.id}, "products_of")
            products = [_ref(r, "Product") for r in rows.rows]
        else:
            products = [start]
        for p in products:
            entities[p.id] = p
        if not products:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"{start.name} buys no products recorded in the graph.",
                entities=list(entities.values()),
            )

        rows = await self._read(
            q_upstream(args.max_depth), {"products": [p.id for p in products]}, "upstream"
        )
        if not rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"No supplier relationships are recorded upstream of {start.name}.",
                entities=list(entities.values()),
                query_ms=rows.elapsed_ms,
            )
        active = await self._active_targets()

        by_country: dict[str, set[str]] = defaultdict(set)
        tiers: dict[int, set[str]] = defaultdict(set)
        suppliers: dict[str, dict[str, Any]] = {}
        for r in rows.rows:
            sid = r["supplier_id"]
            entities[sid] = EntityRef(id=sid, name=r["supplier"], kind="Supplier")
            entities[r["part_id"]] = EntityRef(id=r["part_id"], name=r["part"], kind="Part")
            if r["country"]:
                by_country[r["country"]].add(sid)
            tiers[r["tier"]].add(sid)
            s = suppliers.setdefault(
                sid,
                {
                    "id": sid,
                    "name": r["supplier"],
                    "region": r["region"],
                    "country": r["country"],
                    "tier": r["tier"],
                    "parts": [],
                    "active_disruptions": [],
                },
            )
            s["tier"] = min(s["tier"], r["tier"])
            s["parts"].append({"part": r["part"], "share": r["share"]})
            for target in {sid, r["region_id"], *r["ports"]}:
                for d in active.get(target, []):
                    entities[d["id"]] = EntityRef(id=d["id"], name=d["name"], kind="Disruption")
                    if d["name"] not in s["active_disruptions"]:
                        s["active_disruptions"].append(d["name"])

        disrupted = sorted(
            (s for s in suppliers.values() if s["active_disruptions"]), key=lambda s: -s["tier"]
        )
        countries = sorted(
            ({"country": c, "suppliers": len(ids)} for c, ids in by_country.items()),
            key=lambda x: -x["suppliers"],
        )
        deepest = max(tiers)
        summary = (
            f"{start.name} depends on {len(suppliers)} suppliers across {len(countries)} "
            f"countries, up to tier {deepest}. Most suppliers: "
            + ", ".join(f"{c['country']} ({c['suppliers']})" for c in countries[:4])
            + "."
        )
        if disrupted:
            summary += (
                " Under active disruption: "
                + "; ".join(
                    f"{s['name']} (tier {s['tier']}, {', '.join(s['active_disruptions'])})"
                    for s in disrupted
                )
                + "."
            )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={
                "start": start.model_dump(),
                "products": [p.model_dump() for p in products],
                "countries": countries,
                "suppliers_by_tier": {str(t): len(ids) for t, ids in sorted(tiers.items())},
                "disrupted_suppliers": disrupted,
                "suppliers": sorted(suppliers.values(), key=lambda s: (s["tier"], s["name"]))[:200],
            },
            entities=list(entities.values()),
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- single points

    async def single_points_of_failure(self, args: SinglePointsIn) -> ToolResult:
        tool = "single_points_of_failure"
        entities: dict[str, EntityRef] = {}
        product_id = None
        if args.product:
            res = await self.resolve(args.product, ["Product"])
            if res.entity is None:
                return self._unresolved(tool, args.product, res, ["Product"])
            product_id = res.entity.id
            entities[product_id] = res.entity
        rows = await self._read(
            q_single_points(args.max_depth), {"product": product_id}, "single_points"
        )
        items = []
        for r in rows.rows:
            revenue = sum(p["revenue"] or 0 for p in r["products"])
            items.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "risk": "single_source" if len(r["suppliers"]) == 1 else "single_country",
                    "suppliers": [s["name"] for s in r["suppliers"]],
                    "countries": r["countries"],
                    "inventory_days": r["inventory_days"],
                    "products": [p["name"] for p in r["products"]],
                    "revenue_exposed": revenue,
                    "revenue_exposed_display": self._fmt(revenue),
                }
            )
            entities[r["id"]] = EntityRef(id=r["id"], name=r["name"], kind="Part")
            for s in r["suppliers"]:
                entities[s["id"]] = EntityRef(id=s["id"], name=s["name"], kind="Supplier")
            for p in r["products"]:
                entities[p["id"]] = EntityRef(id=p["id"], name=p["name"], kind="Product")
        items.sort(key=lambda x: (x["risk"] != "single_source", -x["revenue_exposed"]))
        # Totals are counted before the list is trimmed, so callers never mistake the
        # page size for the real number of concentrated parts.
        totals = {
            "total": len(items),
            "single_source_total": sum(1 for i in items if i["risk"] == "single_source"),
            "single_country_total": sum(1 for i in items if i["risk"] == "single_country"),
        }
        items = items[: args.limit]
        scope = f" in {entities[product_id].name}" if product_id else ""
        if not items:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"No single-source or single-country parts found{scope}.",
                entities=list(entities.values()),
                query_ms=rows.elapsed_ms,
            )
        summary = (
            f"{totals['total']} concentrated part(s){scope} "
            f"({totals['single_source_total']} single-source, "
            f"{totals['single_country_total']} single-country). Top: "
            + "; ".join(
                f"{i['name']} ({i['risk'].replace('_', ' ')}: {', '.join(i['suppliers'])}; "
                f"{i['revenue_exposed_display']}/yr downstream)"
                for i in items[:6]
            )
            + "."
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"parts": items, "currency": self.currency, **totals},
            entities=list(entities.values()),
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- alternates

    async def alternate_suppliers(self, args: AlternateSuppliersIn) -> ToolResult:
        tool = "alternate_suppliers"
        res = await self.resolve(args.part, ["Part"])
        if res.entity is None:
            return self._unresolved(tool, args.part, res, ["Part"])
        part = res.entity
        rows = await self._read(Q_SUPPLIERS_OF_PART, {"id": part.id}, "suppliers_of_part")
        entities = {part.id: part}
        if not rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"{part.name} has no supplier recorded in the graph (built in-house?).",
                entities=[part],
                query_ms=rows.elapsed_ms,
            )
        active = await self._active_targets()
        ours = {s["id"] for s in rows.rows}
        hits = {r["id"]: r for r in await self._suppliers_hit(sorted(active)) if r["id"] in ours}
        suppliers = []
        for s in rows.rows:
            entities[s["id"]] = EntityRef(id=s["id"], name=s["name"], kind="Supplier")
            hit = hits.get(s["id"])
            status = "available"
            names: list[str] = []
            if hit:
                status = "disrupted" if hit["reason"] in HIT_REASONS else "degraded"
                for target in {s["id"], hit["region_id"], *hit["via"]}:
                    for d in active.get(target, []):
                        entities[d["id"]] = EntityRef(id=d["id"], name=d["name"], kind="Disruption")
                        if d["name"] not in names:
                            names.append(d["name"])
            suppliers.append({**s, "status": status, "active_disruptions": names})
        order = {"available": 0, "degraded": 1, "disrupted": 2}
        suppliers.sort(key=lambda s: (order[s["status"]], s["lead_time_days"]))
        available = [s for s in suppliers if s["status"] == "available"]
        summary = (
            f"{part.name} has {len(suppliers)} supplier(s): "
            + "; ".join(
                f"{s['name']} ({s['country']}, {round(s['share'] * 100)}% share, "
                f"{s['lead_time_days']}d lead, {s['status']})"
                for s in suppliers
            )
            + "."
        )
        if not available:
            summary += " No supplier is currently free of active disruptions."
        elif len(suppliers) == 1:
            summary += " It is single-sourced: there is no qualified alternate in the graph."
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"part": part.model_dump(), "suppliers": suppliers, "currency": self.currency},
            entities=list(entities.values()),
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- trace

    async def trace_connection(self, args: TraceConnectionIn) -> ToolResult:
        tool = "trace_connection"
        a = await self.resolve(args.source)
        if a.entity is None:
            return self._unresolved(tool, args.source, a, None)
        b = await self.resolve(args.target)
        if b.entity is None:
            return self._unresolved(tool, args.target, b, None)
        rows = await self._read(
            Q_TRACE,
            {"a": a.entity.id, "b": b.entity.id, "rels": TRACE_RELS, "hops": args.max_hops},
            "trace",
        )
        first = rows.first()
        if not first:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"Both exist, but no supply-chain path within {args.max_hops} hops "
                f"connects {a.entity.name} and {b.entity.name}.",
                entities=[a.entity, b.entity],
                query_ms=rows.elapsed_ms,
            )
        nodes = [EntityRef(**n) for n in first["nodes"]]
        name = {n.id: n.name for n in nodes}
        hops = []
        rendered = nodes[0].name
        for i, rel in enumerate(first["rels"]):
            forward = rel["src"] == nodes[i].id
            hops.append(
                {
                    "from": name[rel["src"]],
                    "relationship": rel["type"],
                    "to": name[rel["dst"]],
                    "direction": "forward" if forward else "reverse",
                }
            )
            arrow = f" -[{rel['type']}]-> " if forward else f" <-[{rel['type']}]- "
            rendered += arrow + nodes[i + 1].name
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=f"{len(hops)}-hop connection: {rendered}.",
            data={"hops": hops, "rendered": rendered},
            entities=nodes,
            paths=[nodes],
            query_ms=rows.elapsed_ms,
            rows=1,
        )

    # ----------------------------------------------------------------- lead time

    async def critical_lead_time(self, args: CriticalLeadTimeIn) -> ToolResult:
        tool = "critical_lead_time"
        res = await self.resolve(args.product, ["Product"])
        if res.entity is None:
            return self._unresolved(tool, args.product, res, ["Product"])
        prod = res.entity
        params = {"product": prod.id}
        chains_rows = await self._read(q_bom_chains(args.max_depth), params, "bom_chains")
        sup_rows = await self._read(q_bom_suppliers(args.max_depth), params, "bom_suppliers")
        if not chains_rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"No bill of materials is recorded for {prod.name}.",
                entities=[prod],
            )
        part_info: dict[str, dict[str, Any]] = {}
        for r in sup_rows.rows:
            sups = [s for s in r["suppliers"] if s]
            primary = max(sups, key=lambda s: (s["share"], -s["lead"]), default=None)
            fastest = min(sups, key=lambda s: s["lead"], default=None)
            part_info[r["id"]] = {"name": r["name"], "primary": primary, "fastest": fastest}

        chains = {tuple(r["chain"]) for r in chains_rows.rows if r["chain"]}
        # drop chains that are the downstream tail of a longer chain
        maximal = [
            c for c in chains if not any(len(o) > len(c) and o[-len(c) :] == c for o in chains)
        ]
        entities: dict[str, EntityRef] = {prod.id: prod}
        scored = []
        for chain in maximal:
            stages = []
            total = best = 0
            for pid in chain:
                info = part_info.get(pid, {"name": pid, "primary": None, "fastest": None})
                primary, fastest = info["primary"], info["fastest"]
                lead = primary["lead"] if primary else 0
                total += lead
                best += fastest["lead"] if fastest else 0
                if primary:
                    entities[primary["id"]] = EntityRef(
                        id=primary["id"], name=primary["name"], kind="Supplier"
                    )
                stages.append(
                    {
                        "part": info["name"],
                        "primary_supplier": primary["name"] if primary else None,
                        "lead_time_days": lead,
                    }
                )
            scored.append(
                {"total_days": total, "best_case_days": best, "stages": stages, "_ids": chain}
            )
        scored.sort(key=lambda c: -c["total_days"])
        top = scored[: args.top_k]
        paths = []
        for c in top:
            path = [
                EntityRef(id=pid, name=part_info.get(pid, {"name": pid})["name"], kind="Part")
                for pid in c.pop("_ids")
            ]
            for e in path:
                entities[e.id] = e
            paths.append([*path, prod])
        crit = top[0]
        summary = (
            f"Critical sourcing path for {prod.name}: "
            + " -> ".join(f"{s['part']} ({s['lead_time_days']}d)" for s in crit["stages"])
            + f" = {crit['total_days']} days end to end with primary suppliers "
            f"({crit['best_case_days']} days using the fastest qualified supplier at each stage)."
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"product": prod.model_dump(), "chains": top, "chains_considered": len(maximal)},
            entities=list(entities.values()),
            paths=paths,
            query_ms=chains_rows.elapsed_ms + sup_rows.elapsed_ms,
            rows=len(chains_rows),
        )

    # ----------------------------------------------------------------- criticality

    async def supplier_criticality(self, args: SupplierCriticalityIn) -> ToolResult:
        tool = "supplier_criticality"
        rows = await self._read(q_criticality(args.max_depth), None, "criticality")
        if not rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary="No supplier reaches a product in the graph.",
            )
        status = await self.disrupted_supplier_ids()
        agg: dict[str, dict[str, Any]] = {}
        for r in rows.rows:
            a = agg.setdefault(
                r["id"],
                {"id": r["id"], "name": r["name"], "reach": {}, "sole": {}, "sole_parts": []},
            )
            for p in r["products"]:
                a["reach"][p["id"]] = p
                if r["others"] == 0:
                    a["sole"][p["id"]] = p
            if r["others"] == 0:
                a["sole_parts"].append(r["part"])
        ranked = []
        for a in agg.values():
            reach = sum(p["revenue"] or 0 for p in a["reach"].values())
            sole = sum(p["revenue"] or 0 for p in a["sole"].values())
            ranked.append(
                {
                    "id": a["id"],
                    "name": a["name"],
                    "sole_source_revenue": sole,
                    "sole_source_revenue_display": self._fmt(sole),
                    "revenue_reach": reach,
                    "revenue_reach_display": self._fmt(reach),
                    "sole_source_parts": a["sole_parts"],
                    "products": sorted(p["name"] for p in a["reach"].values()),
                    "status": status.get(a["id"], "available"),
                }
            )
        ranked.sort(key=lambda x: (-x["sole_source_revenue"], -x["revenue_reach"]))
        ranked = ranked[: args.limit]
        entities = [EntityRef(id=x["id"], name=x["name"], kind="Supplier") for x in ranked]
        summary = (
            "Most critical suppliers (revenue that depends on them alone, then total reach): "
            + "; ".join(
                f"{x['name']} ({x['sole_source_revenue_display']} sole-sourced, "
                f"{x['revenue_reach_display']} reach"
                + (f", {x['status']} now" if x["status"] != "available" else "")
                + ")"
                for x in ranked[:6]
            )
            + "."
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"suppliers": ranked, "currency": self.currency},
            entities=entities,
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- disruptions

    async def active_disruptions(self, args: ActiveDisruptionsIn) -> ToolResult:
        tool = "active_disruptions"
        rows = await self._read(Q_DISRUPTIONS, {"all": args.include_inactive}, "disruptions")
        if not rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary="No disruptions are recorded in the graph.",
                query_ms=rows.elapsed_ms,
            )
        entities: dict[str, EntityRef] = {}
        items = []
        for r in rows.rows:
            affects = [a for a in r["affects"] if a]
            entities[r["id"]] = EntityRef(id=r["id"], name=r["name"], kind="Disruption")
            for a in affects:
                entities[a["id"]] = EntityRef(**a)
            items.append({**r, "affects": affects})
        summary = (
            "Disruptions: "
            + "; ".join(
                f"{d['name']} (severity {d['severity']}, {d['category']}, until "
                f"{d['expected_end'] or 'unknown'}; "
                f"affects {', '.join(a['name'] for a in d['affects'])})"
                for d in items
            )
            + "."
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"disruptions": items},
            entities=list(entities.values()),
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- memory

    async def recall_findings(self, args: RecallFindingsIn) -> ToolResult:
        tool = "recall_findings"
        res = await self.resolve(args.entity)
        if res.entity is None:
            return self._unresolved(tool, args.entity, res, None)
        rows = await self._read(q_recall(args.limit), {"id": res.entity.id}, "recall")
        if not rows.rows:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"No stored findings about {res.entity.name}.",
                entities=[res.entity],
                query_ms=rows.elapsed_ms,
            )
        summary = f"{len(rows)} stored finding(s) about {res.entity.name}: " + " | ".join(
            f"[{r['created_at'][:10]}] {r['text']}" for r in rows.rows
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.FROM_MEMORY,
            summary=summary,
            data={"entity": res.entity.model_dump(), "findings": rows.rows},
            entities=[res.entity],
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    async def record_finding(self, args: RecordFindingIn) -> ToolResult:
        tool = "record_finding"
        res = await self.resolve(args.entity)
        if res.entity is None:
            return self._unresolved(tool, args.entity, res, None)
        fid = f"FND-{uuid.uuid4().hex[:16]}"
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        rows = await self.store.write(
            self.tenant,
            Q_RECORD,
            {
                "id": res.entity.id,
                "fid": fid,
                "text": args.text.strip(),
                "ts": ts,
                "sid": self.session_id,
                "author": "agent",
            },
            op="record_finding",
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=f"Stored finding {fid} about {res.entity.name}.",
            data={"id": fid, "created_at": ts},
            entities=[res.entity],
            query_ms=rows.elapsed_ms,
            rows=len(rows),
        )

    # ----------------------------------------------------------------- documents

    async def search_documents(self, args: SearchDocumentsIn) -> ToolResult:
        tool = "search_documents"
        count = await self._read(Q_CHUNK_COUNT, None, "chunk_count")
        if not (count.first() or {}).get("n"):
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary="No document passages are indexed in this workspace yet.",
            )
        focus: EntityRef | None = None
        if args.entity:
            res = await self.resolve(args.entity, ["Supplier", "Port", "Region", "Disruption"])
            if res.entity is None:
                return self._unresolved(
                    tool, args.entity, res, ["Supplier", "Port", "Region", "Disruption"]
                )
            focus = res.entity
        if self._embed is None:
            raise ToolInputError("document search needs an embedding provider (OPENAI_API_KEY)")
        [vector] = await self._embed([args.query])

        pool = args.limit * (6 if focus else 4)
        rows = await self._read(Q_SEARCH_CHUNKS, {"k": pool, "vector": vector}, "search_documents")
        terms = query_terms(args.query)
        hits = []
        for r in rows.rows:
            similarity = round(1.0 - float(r["distance"]), 3)
            if similarity < self._min_similarity:
                continue
            mentions = [m for m in r["mentions"] if m]
            disruptions = [d for d in r["disruptions"] if d]
            if focus and not (
                focus.id in {m["id"] for m in mentions}
                or focus.id in set(r["affected_ids"])
                or focus.id in {d["id"] for d in disruptions}
            ):
                continue
            keywords = keyword_overlap(terms, r["text"])
            hits.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "page": r["page"],
                    # Hybrid relevance: meaning (vector) plus the query's own words, so the
                    # passage that both means and says it ranks first.
                    "score": round(0.7 * similarity + 0.3 * keywords, 3),
                    "similarity": similarity,
                    "keyword_match": round(keywords, 3),
                    "document": r["document"],
                    "disruptions": disruptions,
                    "mentions": mentions,
                }
            )
        hits.sort(key=lambda h: -h["score"])
        hits = hits[: args.limit]
        entities: dict[str, EntityRef] = {}
        for h in hits:
            for e in h["mentions"]:
                entities[e["id"]] = EntityRef(**e)
            for d in h["disruptions"]:
                entities[d["id"]] = EntityRef(id=d["id"], name=d["name"], kind="Disruption")
        if focus:
            entities[focus.id] = focus
        scope = f" about {focus.name}" if focus else ""
        if not hits:
            return ToolResult(
                tool=tool,
                outcome=Outcome.NOT_IN_GRAPH,
                summary=f"No indexed passage{scope} is relevant to '{args.query}'.",
                entities=list(entities.values()),
                query_ms=rows.elapsed_ms,
            )
        summary = f"{len(hits)} relevant passage(s){scope}: " + " | ".join(
            f"[{h['document']['filename']} p.{h['page']}] {h['text'][:180]}"
            + ("…" if len(h["text"]) > 180 else "")
            for h in hits[:3]
        )
        return ToolResult(
            tool=tool,
            outcome=Outcome.ANSWERED,
            summary=summary,
            data={"query": args.query, "passages": hits},
            entities=list(entities.values()),
            query_ms=rows.elapsed_ms,
            rows=len(hits),
        )

    # ----------------------------------------------------------------- catalog

    async def entity_names(self) -> list[EntityRef]:
        rows = await self._read(Q_NAMES, None, "names")
        return [_ref(r) for r in rows.rows]


def toolbox_for(
    settings: Any,
    store: GraphStore,
    tenant: str,
    *,
    provider: Any | None = None,
    session_id: str | None = None,
) -> SupplyGraphTools:
    """Build a toolbox wired to the deployment's settings and (optional) LLM provider."""
    embedder = provider.embed if provider is not None and provider.configured() else None
    return SupplyGraphTools(
        store,
        tenant,
        session_id=session_id,
        currency=settings.base_currency,
        embedder=embedder,
        min_passage_similarity=settings.min_passage_similarity,
    )
