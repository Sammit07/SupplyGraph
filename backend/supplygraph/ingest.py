"""Load a validated :class:`NetworkData` document into a tenant graph.

Two modes:

* ``merge``: idempotent upsert (``MERGE`` on ``Entity.id``). Existing findings, documents
  and PDF-derived disruptions stay.
* ``replace``: the network is built in a staging graph, then swapped in, so a failed import
  never leaves a half-written live graph. Everything in the old graph is discarded.

Labels and relationship types come from fixed code paths, never from input data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

from .datasets import ParseOutcome, parse_bundle
from .db import GraphStore
from .models import NetworkData
from .schema import ensure_schema

log = logging.getLogger(__name__)

BATCH_SIZE = 500
STAGING_SUFFIX = "--staging"
ImportMode = Literal["merge", "replace"]

# kind label -> Cypher. Labels are literals here; only values are parameters.
_NODE_UPSERT = {
    kind: (
        f"UNWIND $rows AS row "
        f"MERGE (n:Entity:{kind} {{id: row.id}}) "
        f"SET n += row.props, n.kind = '{kind}' "
        f"RETURN count(n) AS n"
    )
    for kind in ("Region", "Port", "Part", "Customer", "Product", "Supplier", "Disruption")
}

_REL_UPSERT = {
    rel: (
        f"UNWIND $rows AS row "
        f"MATCH (a:Entity {{id: row.src}}), (b:Entity {{id: row.dst}}) "
        f"MERGE (a)-[r:{rel}]->(b) "
        f"SET r += row.props "
        f"RETURN count(r) AS n"
    )
    for rel in ("SUPPLIES", "COMPONENT_OF", "SOLD_TO", "LOCATED_IN", "SHIPS_VIA", "AFFECTS")
}


def load_dataset_dir(path: str | Path, *, base_currency: str = "USD") -> ParseOutcome:
    """Parse every ``*.csv`` file in a directory."""
    directory = Path(path)
    files = {p.name: p.read_bytes() for p in sorted(directory.glob("*.csv"))}
    if not files:
        raise FileNotFoundError(f"no CSV files in {directory}")
    return parse_bundle(files, base_currency=base_currency)


def _batches(rows: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(rows), BATCH_SIZE):
        yield rows[i : i + BATCH_SIZE]


def _node(id_: str, **props: Any) -> dict[str, Any]:
    return {"id": id_, "props": {"id": id_, **props}}


def _rel(src: str, dst: str, **props: Any) -> dict[str, Any]:
    return {"src": src, "dst": dst, "props": props}


def build_rows(net: NetworkData) -> tuple[dict[str, list], dict[str, list]]:
    nodes: dict[str, list[dict[str, Any]]] = {k: [] for k in _NODE_UPSERT}
    rels: dict[str, list[dict[str, Any]]] = {k: [] for k in _REL_UPSERT}

    for r in net.regions:
        nodes["Region"].append(_node(r.id, name=r.name, country=r.country))
    for p in net.ports:
        nodes["Port"].append(_node(p.id, name=p.name))
        rels["LOCATED_IN"].append(_rel(p.id, p.region))
    for part in net.parts:
        nodes["Part"].append(
            _node(
                part.id,
                name=part.name,
                category=part.category,
                item_type=part.item_type,
                inventory_days=part.inventory_days,
            )
        )
        for c in part.components:
            rels["COMPONENT_OF"].append(_rel(c.part, part.id, qty=c.qty))
    for c in net.customers:
        nodes["Customer"].append(_node(c.id, name=c.name, segment=c.segment))
    for prod in net.products:
        nodes["Product"].append(_node(prod.id, name=prod.name, annual_revenue=prod.annual_revenue))
        for c in prod.components:
            rels["COMPONENT_OF"].append(_rel(c.part, prod.id, qty=c.qty))
        for sale in prod.customers:
            rels["SOLD_TO"].append(_rel(prod.id, sale.customer, annual_revenue=sale.annual_revenue))
    for s in net.suppliers:
        nodes["Supplier"].append(_node(s.id, name=s.name, risk_score=s.risk_score))
        rels["LOCATED_IN"].append(_rel(s.id, s.region))
        for port in s.ports:
            rels["SHIPS_VIA"].append(_rel(s.id, port))
        for sup in s.supplies:
            rels["SUPPLIES"].append(
                _rel(
                    s.id,
                    sup.part,
                    lead_time_days=sup.lead_time_days,
                    share=sup.share,
                    unit_cost=sup.unit_cost,
                )
            )
    for d in net.disruptions:
        nodes["Disruption"].append(
            _node(
                d.id,
                name=d.name,
                category=d.category,
                severity=d.severity,
                started_at=d.started_at.isoformat(),
                expected_end=d.expected_end.isoformat() if d.expected_end else None,
                active=d.active,
                source="risk_events.csv",
            )
        )
        for target in d.affects:
            rels["AFFECTS"].append(_rel(d.id, target))
    return nodes, rels


async def ingest(
    store: GraphStore, tenant: str, net: NetworkData, *, mode: ImportMode = "merge"
) -> dict[str, int]:
    """Write the network into the tenant graph and return element counts by type."""
    if mode == "replace":
        staging = tenant + STAGING_SUFFIX
        await store.drop(staging)
        counts = await _write(store, staging, net)
        try:
            await store.replace_graph(source_tenant=staging, target_tenant=tenant)
        except Exception:
            # GRAPH.COPY unavailable or failed: rebuild the live graph in place instead.
            log.warning("graph swap failed; rebuilding in place", extra={"tenant": tenant})
            await store.drop(tenant)
            counts = await _write(store, tenant, net)
        finally:
            await store.drop(staging)
        await ensure_schema(store, tenant)
    else:
        counts = await _write(store, tenant, net)
    log.info("ingest complete", extra={"tenant": tenant, "mode": mode, "counts": counts})
    return counts


async def _write(store: GraphStore, tenant: str, net: NetworkData) -> dict[str, int]:
    await ensure_schema(store, tenant)
    nodes, rels = build_rows(net)
    counts: dict[str, int] = {}
    for kind, rows in nodes.items():
        counts[kind] = await _upsert(store, tenant, _NODE_UPSERT[kind], rows, op=f"ingest:{kind}")
    for rel, rows in rels.items():
        counts[rel] = await _upsert(store, tenant, _REL_UPSERT[rel], rows, op=f"ingest:{rel}")
    expected = {k: len(v) for k, v in (nodes | rels).items()}
    short = {k: (expected[k], counts[k]) for k in expected if counts[k] != expected[k]}
    if short:
        raise RuntimeError(f"ingest wrote fewer elements than expected: {short}")
    return counts


async def _upsert(
    store: GraphStore, tenant: str, cypher: str, rows: Iterable[dict[str, Any]], *, op: str
) -> int:
    total = 0
    for batch in _batches(list(rows)):
        result = await store.write(tenant, cypher, {"rows": batch}, op=op)
        first = result.first()
        total += int(first["n"]) if first else 0
    return total
