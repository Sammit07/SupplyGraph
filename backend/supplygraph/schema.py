"""Graph schema: labels, relationship types, indexes and constraints.

Every resolvable node carries the shared label ``Entity`` plus its own kind label, and a
``kind`` property mirroring it. That gives one unique constraint on ``Entity.id`` and one
full-text index on ``Entity.name`` for entity resolution, while kind-specific patterns
(``(:Supplier)-[:SUPPLIES]->(:Part)``) still use their own labels.

    (:Supplier)-[:SUPPLIES {lead_time_days, share, unit_cost_usd}]->(:Part)
    (:Part)-[:COMPONENT_OF {qty}]->(:Part | :Product)       bill of materials
    (:Product)-[:SOLD_TO {annual_revenue_usd}]->(:Customer)
    (:Supplier)-[:LOCATED_IN]->(:Region)
    (:Port)-[:LOCATED_IN]->(:Region)
    (:Supplier)-[:SHIPS_VIA]->(:Port)
    (:Disruption)-[:AFFECTS]->(:Region | :Port | :Supplier)
    (:Finding)-[:ABOUT]->(:Entity)                          agent memory
    (:Document)-[:REPORTS]->(:Disruption)                   PDF provenance
"""

from __future__ import annotations

import logging

from redis.exceptions import ResponseError

from .db import GraphStore

log = logging.getLogger(__name__)

ENTITY_KINDS: tuple[str, ...] = (
    "Supplier",
    "Part",
    "Product",
    "Customer",
    "Region",
    "Port",
    "Disruption",
)
# Relationships that carry material, money or location. Used for path explanations.
SUPPLY_RELS: tuple[str, ...] = ("SUPPLIES", "COMPONENT_OF", "SOLD_TO", "LOCATED_IN", "SHIPS_VIA")

_IGNORABLE = ("already indexed", "already exists", "index already", "constraint already")


async def ensure_schema(store: GraphStore, tenant: str) -> None:
    """Create indexes and constraints. Safe to call repeatedly."""
    graph = await store.raw_graph(tenant)

    async def attempt(desc: str, coro) -> None:
        try:
            await coro
            log.info("schema object created", extra={"object": desc, "tenant": tenant})
        except ResponseError as exc:
            if any(token in str(exc).lower() for token in _IGNORABLE):
                return
            raise

    # Range indexes first: the first write also materialises the graph key, which
    # GRAPH.CONSTRAINT requires.
    await attempt("range Entity.id", graph.create_node_range_index("Entity", "id"))
    await attempt("range Entity.kind", graph.create_node_range_index("Entity", "kind"))
    await attempt("range Finding.id", graph.create_node_range_index("Finding", "id"))
    await attempt("range Disruption.active", graph.create_node_range_index("Disruption", "active"))
    await attempt("fulltext Entity.name", graph.create_node_fulltext_index("Entity", "name"))
    await attempt("unique Entity.id", graph.create_node_unique_constraint("Entity", "id"))
    await attempt("unique Finding.id", graph.create_node_unique_constraint("Finding", "id"))
    await attempt("range Document.sha256", graph.create_node_range_index("Document", "sha256"))
