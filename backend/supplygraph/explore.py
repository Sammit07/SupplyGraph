"""Read models for the UI: network view, entity detail, search, overview and findings.

Everything here is computed from the graph at request time; the overview reuses the same
tools the agent uses, so the dashboard and the agent can never disagree.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any

from .config import Settings
from .db import GraphStore
from .models import Outcome
from .schema import SUPPLY_RELS
from .tools import (
    ActiveDisruptionsIn,
    CombinedImpactIn,
    DisruptionImpactIn,
    SinglePointsIn,
    SupplierCriticalityIn,
    SupplyGraphTools,
    ToolInputError,
    _bound,
    fulltext_query,
)

_REL_PATTERN = "|".join(SUPPLY_RELS) + "|AFFECTS"

Q_STATS_NODES = "MATCH (n:Entity) RETURN n.kind AS kind, count(n) AS n"
Q_STATS_RELS = "MATCH (:Entity)-[r]->(:Entity) RETURN type(r) AS type, count(r) AS n"
Q_STATS_FINDINGS = "MATCH (f:Finding) RETURN count(f) AS n"
Q_STATS_DOCS = "MATCH (d:Document) RETURN count(d) AS n"

Q_NETWORK_NODES = """
MATCH (n:Entity)
WHERE $include_inactive OR n.kind <> 'Disruption' OR n.active = true
RETURN n.id AS id, n.name AS name, n.kind AS kind
LIMIT $limit
"""
Q_NETWORK_LINKS = f"""
MATCH (a:Entity)-[r:{_REL_PATTERN}]->(b:Entity)
WHERE a.id IN $ids AND b.id IN $ids
RETURN a.id AS source, b.id AS target, type(r) AS type
"""


def q_neighborhood(depth: int) -> str:
    return f"""
MATCH p = (c:Entity {{id: $id}})-[:{_REL_PATTERN}*1..{_bound(depth, 3)}]-(m:Entity)
WITH relationships(p) AS rs
LIMIT 4000
UNWIND rs AS r
RETURN DISTINCT startNode(r).id AS source, endNode(r).id AS target, type(r) AS type
"""


Q_NODES_BY_ID = """
MATCH (n:Entity) WHERE n.id IN $ids
RETURN n.id AS id, n.name AS name, n.kind AS kind
"""

Q_ENTITY = """
MATCH (n:Entity {id: $id})
OPTIONAL MATCH (n)-[r]-(m:Entity)
RETURN properties(n) AS props,
       collect(CASE WHEN m IS NULL THEN NULL ELSE
         {type: type(r), outgoing: startNode(r) = n, id: m.id, name: m.name, kind: m.kind,
          props: properties(r)} END) AS rels
"""

Q_ENTITY_DOCS = """
MATCH (doc:Document)-[:REPORTS]->(d:Disruption {id: $id})
RETURN doc.id AS id, doc.filename AS filename, doc.issuer AS issuer, doc.uploaded_at AS uploaded_at
"""

# Passages from the documents that reported a disruption, ranked by how many of the
# disruption's affected entities each passage mentions.
Q_DISRUPTION_PASSAGES = """
MATCH (d:Disruption {id: $id})<-[:REPORTS]-(doc:Document)<-[:PART_OF]-(c:Chunk)
OPTIONAL MATCH (d)-[:AFFECTS]->(e:Entity)-[:MENTIONED_IN]->(c)
WITH c, doc, count(e) AS hits
RETURN c.id AS id, c.text AS text, c.page AS page, doc.filename AS filename, hits
ORDER BY hits DESC, c.ordinal ASC
LIMIT 3
"""

Q_ENTITY_PASSAGES = """
MATCH (:Entity {id: $id})-[:MENTIONED_IN]->(c:Chunk)-[:PART_OF]->(doc:Document)
RETURN c.id AS id, c.text AS text, c.page AS page, doc.filename AS filename
ORDER BY doc.uploaded_at DESC, c.ordinal ASC
LIMIT 5
"""

Q_SEARCH = """
CALL db.idx.fulltext.queryNodes('Entity', $q) YIELD node, score
WITH node, score
WHERE $kinds IS NULL OR node.kind IN $kinds
RETURN node.id AS id, node.name AS name, node.kind AS kind
ORDER BY score DESC
LIMIT 20
"""

Q_FINDINGS = """
MATCH (f:Finding)-[:ABOUT]->(e:Entity)
RETURN f.id AS id, f.text AS text, f.created_at AS created_at, f.session_id AS session_id,
       f.author AS author, {id: e.id, name: e.name, kind: e.kind} AS entity
ORDER BY f.created_at DESC
LIMIT 500
"""
Q_DELETE_FINDING = "MATCH (f:Finding {id: $id}) DETACH DELETE f RETURN 1 AS ok"
Q_SET_DISRUPTION = """
MATCH (d:Disruption {id: $id})
SET d.active = $active, d.status_changed_at = $ts
RETURN d.id AS id, d.active AS active
"""


class ExploreService:
    _overview_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    OVERVIEW_TTL = 20.0

    def __init__(self, settings: Settings, store: GraphStore, tenant: str) -> None:
        self.settings = settings
        self.store = store
        self.tenant = tenant
        self.tools = SupplyGraphTools(store, tenant, currency=settings.base_currency)

    async def _read(self, cypher: str, params: dict[str, Any] | None, op: str):
        return await self.store.read(self.tenant, cypher, params, op=op)

    @classmethod
    def invalidate(cls, tenant: str) -> None:
        cls._overview_cache.pop(tenant, None)

    # ----------------------------------------------------------------- stats

    async def stats(self) -> dict[str, Any]:
        nodes, rels, findings, docs = await asyncio.gather(
            self._read(Q_STATS_NODES, None, "stats:nodes"),
            self._read(Q_STATS_RELS, None, "stats:rels"),
            self._read(Q_STATS_FINDINGS, None, "stats:findings"),
            self._read(Q_STATS_DOCS, None, "stats:docs"),
        )
        return {
            "graph": self.store.graph_id(self.tenant),
            "nodes": {r["kind"]: r["n"] for r in nodes.rows},
            "relationships": {r["type"]: r["n"] for r in rels.rows},
            "findings": (findings.first() or {}).get("n", 0),
            "documents": (docs.first() or {}).get("n", 0),
        }

    # ----------------------------------------------------------------- network

    async def network(self, *, include_inactive: bool = False) -> dict[str, Any]:
        limit = self.settings.network_max_nodes
        nodes = await self._read(
            Q_NETWORK_NODES, {"limit": limit + 1, "include_inactive": include_inactive}, "network"
        )
        truncated = len(nodes.rows) > limit
        node_rows = nodes.rows[:limit]
        ids = [n["id"] for n in node_rows]
        links, status = await asyncio.gather(
            self._read(Q_NETWORK_LINKS, {"ids": ids}, "network:links"),
            self.tools.disrupted_supplier_ids(),
        )
        return self._graph_payload(node_rows, links.rows, status, truncated=truncated)

    async def neighborhood(self, entity_id: str, depth: int) -> dict[str, Any]:
        try:
            cypher = q_neighborhood(depth)
        except ToolInputError as exc:
            raise ValueError(str(exc)) from exc
        links = await self._read(cypher, {"id": entity_id}, "neighborhood")
        ids = {entity_id} | {x["source"] for x in links.rows} | {x["target"] for x in links.rows}
        nodes = await self._read(Q_NODES_BY_ID, {"ids": sorted(ids)}, "neighborhood:nodes")
        status = await self.tools.disrupted_supplier_ids()
        return self._graph_payload(nodes.rows, links.rows, status, focus=entity_id)

    @staticmethod
    def _graph_payload(
        nodes: list[dict[str, Any]],
        links: list[dict[str, Any]],
        status: dict[str, str],
        *,
        truncated: bool = False,
        focus: str | None = None,
    ) -> dict[str, Any]:
        degree: Counter[str] = Counter()
        affected: set[str] = set()
        for link in links:
            degree[link["source"]] += 1
            degree[link["target"]] += 1
            if link["type"] == "AFFECTS":
                affected.add(link["target"])
        out_nodes = []
        for n in nodes:
            state = status.get(n["id"])
            if n["kind"] == "Disruption":
                state = "disruption"
            elif n["id"] in affected:
                state = state or "affected"
            out_nodes.append({**n, "degree": degree[n["id"]], "status": state or "normal"})
        return {
            "nodes": out_nodes,
            "links": links,
            "truncated": truncated,
            "focus": focus,
        }

    # ----------------------------------------------------------------- entity

    async def entity(self, entity_id: str) -> dict[str, Any] | None:
        rows = await self._read(Q_ENTITY, {"id": entity_id}, "entity")
        first = rows.first()
        if not first or not first["props"]:
            return None
        props = dict(first["props"])
        rels = [r for r in first["rels"] if r]
        result: dict[str, Any] = {
            "id": props.get("id"),
            "name": props.get("name"),
            "kind": props.get("kind"),
            "properties": {k: v for k, v in props.items() if k not in {"id", "name", "kind"}},
            "relationships": sorted(rels, key=lambda r: (r["type"], r["name"] or "")),
        }
        if result["kind"] == "Disruption":
            docs = await self._read(Q_ENTITY_DOCS, {"id": entity_id}, "entity:docs")
            result["documents"] = docs.rows
            passages = await self._read(Q_DISRUPTION_PASSAGES, {"id": entity_id}, "entity:psg")
            result["passages"] = passages.rows
        elif result["kind"] in {"Supplier", "Port", "Region"}:
            passages = await self._read(Q_ENTITY_PASSAGES, {"id": entity_id}, "entity:psg")
            result["passages"] = passages.rows
        if result["kind"] == "Supplier":
            status = await self.tools.disrupted_supplier_ids()
            result["status"] = status.get(entity_id, "available")
        return result

    async def search(self, text: str, kinds: list[str] | None) -> list[dict[str, Any]]:
        query = fulltext_query(text)
        if not query:
            return []
        rows = await self._read(Q_SEARCH, {"q": query, "kinds": kinds or None}, "search")
        return rows.rows

    # ----------------------------------------------------------------- overview

    async def overview(self) -> dict[str, Any]:
        cached = self._overview_cache.get(self.tenant)
        if cached and time.monotonic() - cached[0] < self.OVERVIEW_TTL:
            return cached[1]

        stats, disruptions, spof, critical = await asyncio.gather(
            self.stats(),
            self.tools.active_disruptions(ActiveDisruptionsIn()),
            self.tools.single_points_of_failure(SinglePointsIn(limit=50)),
            self.tools.supplier_criticality(SupplierCriticalityIn(limit=8)),
        )
        events = disruptions.data.get("disruptions", [])[: self.settings.overview_max_disruptions]
        combined, *impacts = await asyncio.gather(
            self.tools.combined_impact(CombinedImpactIn()),
            *(
                self.tools.disruption_impact(DisruptionImpactIn(entity=e["id"], kind="Disruption"))
                for e in events
            ),
        )

        at_risk_products: dict[str, dict[str, Any]] = {}
        cards = []
        for event, impact in zip(events, impacts, strict=True):
            data = impact.data if impact.outcome == Outcome.ANSWERED else {}
            products = data.get("products", [])
            for p in products:
                if p["status"] == "line_stop_risk":
                    at_risk_products[p["id"]] = p
            cards.append(
                {
                    "id": event["id"],
                    "name": event["name"],
                    "category": event["category"],
                    "severity": event["severity"],
                    "started_at": event["started_at"],
                    "expected_end": event["expected_end"],
                    "source": event.get("source"),
                    "affects": event["affects"],
                    "outcome": impact.outcome.value,
                    "summary": impact.summary,
                    "suppliers_hit": len(data.get("suppliers_hit", [])),
                    "suppliers_degraded": len(data.get("suppliers_degraded", [])),
                    "outage_days": data.get("outage_days"),
                    "revenue_at_risk": data.get("revenue_at_risk", 0),
                    "revenue_at_risk_display": data.get(
                        "revenue_at_risk_display", self.tools._fmt(0)
                    ),
                    "line_stop_products": [
                        p["name"] for p in products if p["status"] == "line_stop_risk"
                    ],
                    "constrained_products": [
                        p["name"] for p in products if p["status"] != "line_stop_risk"
                    ],
                    "stockouts": [p["name"] for p in products if p.get("stockout_expected")],
                }
            )
        cards.sort(key=lambda c: (-c["revenue_at_risk"], -c["severity"]))

        # Headline figures come from all active disruptions applied together, because losses
        # compound: two partial outages can leave a part with no supplier at all.
        combined_products = (
            combined.data.get("products", []) if combined.outcome == Outcome.ANSWERED else []
        )
        stopped = {p["id"]: p for p in combined_products if p["status"] == "line_stop_risk"}
        compound_only = [p for pid, p in stopped.items() if pid not in at_risk_products]
        total_at_risk = sum(p["annual_revenue"] for p in stopped.values())
        compound_revenue = sum(p["annual_revenue"] for p in compound_only)
        # Parts left with no supplier only because events combine (not by any single event).
        blocked_alone = {
            p["id"]
            for impact in impacts
            if impact.outcome == Outcome.ANSWERED
            for p in impact.data.get("parts", [])
            if p["share_lost"] >= 0.999
        }
        blocked_parts = [
            p["name"]
            for p in (
                combined.data.get("parts", []) if combined.outcome == Outcome.ANSWERED else []
            )
            if p["share_lost"] >= 0.999 and p["id"] not in blocked_alone
        ]
        total_revenue = await self._total_revenue()
        spof_parts = spof.data.get("parts", []) if spof.outcome == Outcome.ANSWERED else []
        critical_suppliers = (
            critical.data.get("suppliers", []) if critical.outcome == Outcome.ANSWERED else []
        )

        result = {
            "currency": self.settings.base_currency,
            "stats": stats,
            "kpis": {
                "active_disruptions": len(disruptions.data.get("disruptions", [])),
                "revenue_at_risk": total_at_risk,
                "revenue_at_risk_display": self.tools._fmt(total_at_risk),
                "revenue_total": total_revenue,
                "revenue_total_display": self.tools._fmt(total_revenue),
                "products_at_risk": len(stopped),
                "single_source_parts": spof.data.get("single_source_total", 0),
                "single_country_parts": spof.data.get("single_country_total", 0),
                "suppliers_disrupted": len(
                    [
                        s
                        for s in (await self.tools.disrupted_supplier_ids()).values()
                        if s == "disrupted"
                    ]
                ),
            },
            "disruptions": cards,
            "compound": {
                "products": [
                    {"id": p["id"], "name": p["name"], "critical_path": p.get("critical_path")}
                    for p in compound_only
                ],
                "revenue_at_risk": compound_revenue,
                "revenue_at_risk_display": self.tools._fmt(compound_revenue),
                "blocked_parts": blocked_parts,
            },
            "single_points": spof_parts[:8],
            "critical_suppliers": critical_suppliers,
            "suggestions": _suggestions(cards, spof_parts, critical_suppliers),
        }
        self._overview_cache[self.tenant] = (time.monotonic(), result)
        return result

    async def _total_revenue(self) -> float:
        rows = await self._read(
            "MATCH (p:Product) RETURN sum(coalesce(p.annual_revenue, 0)) AS total", None, "revenue"
        )
        return float((rows.first() or {}).get("total") or 0)

    # ----------------------------------------------------------------- findings & status

    async def findings(self) -> list[dict[str, Any]]:
        return (await self._read(Q_FINDINGS, None, "findings")).rows

    async def delete_finding(self, finding_id: str) -> bool:
        rows = await self.store.write(
            self.tenant, Q_DELETE_FINDING, {"id": finding_id}, op="finding:delete"
        )
        return bool(rows.rows)

    async def set_disruption_active(self, disruption_id: str, active: bool) -> dict | None:
        from datetime import UTC, datetime

        rows = await self.store.write(
            self.tenant,
            Q_SET_DISRUPTION,
            {
                "id": disruption_id,
                "active": active,
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            op="disruption:status",
        )
        self.invalidate(self.tenant)
        return rows.first()


def _suggestions(
    cards: list[dict[str, Any]], spof: list[dict[str, Any]], critical: list[dict[str, Any]]
) -> list[str]:
    """Starter questions for the analyst, derived from what the graph currently shows."""
    out: list[str] = []
    if cards:
        out.append(f"What does {cards[0]['name']} put at risk, and which suppliers can cover?")
    if len(cards) > 1:
        out.append("Which products are hit by more than one active disruption?")
    single = [p for p in spof if p["risk"] == "single_source"]
    if single:
        out.append(f"Who else could supply {single[0]['name']}, and how exposed are we?")
    if critical:
        out.append(f"If {critical[0]['name']} went offline for 60 days, what would stop?")
    if spof and spof[0].get("products"):
        out.append(f"What is the longest sourcing lead time for {spof[0]['products'][0]}?")
    return out
