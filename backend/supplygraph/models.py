"""Pydantic models: the network-data contract and the tool-result contract."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"


# --------------------------------------------------------------------------- network contract


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegionIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")


class PortIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    region: str


class ComponentIn(_Strict):
    part: str
    qty: float = Field(gt=0)


class PartIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    category: str
    item_type: str = "COMPONENT"
    inventory_days: int = Field(ge=0, le=3650)
    components: list[ComponentIn] = Field(default_factory=list)


class CustomerIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    segment: str = ""


class SaleIn(_Strict):
    customer: str
    annual_revenue: float = Field(ge=0)


class ProductIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    components: list[ComponentIn] = Field(default_factory=list)
    customers: list[SaleIn] = Field(default_factory=list)

    @property
    def annual_revenue(self) -> float:
        return sum(s.annual_revenue for s in self.customers)


class SupplyIn(_Strict):
    part: str
    lead_time_days: int = Field(ge=0, le=730)
    share: float = Field(ge=0, le=1)
    unit_cost: float = Field(ge=0)


class SupplierIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    region: str
    ports: list[str] = Field(default_factory=list)
    risk_score: float = Field(ge=0, le=1)
    supplies: list[SupplyIn] = Field(default_factory=list)


class DisruptionIn(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=300)
    category: str
    severity: int = Field(ge=1, le=5)
    started_at: date
    expected_end: date | None = None
    active: bool
    affects: list[str] = Field(min_length=1)


class NetworkData(_Strict):
    regions: list[RegionIn]
    ports: list[PortIn]
    parts: list[PartIn]
    customers: list[CustomerIn]
    products: list[ProductIn]
    suppliers: list[SupplierIn]
    disruptions: list[DisruptionIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> NetworkData:
        errors: list[str] = []
        ids: dict[str, str] = {}
        for kind, items in (
            ("Region", self.regions),
            ("Port", self.ports),
            ("Part", self.parts),
            ("Customer", self.customers),
            ("Product", self.products),
            ("Supplier", self.suppliers),
            ("Disruption", self.disruptions),
        ):
            for item in items:
                if item.id in ids:
                    errors.append(f"duplicate id {item.id} ({ids[item.id]} and {kind})")
                ids[item.id] = kind

        def expect(ref: str, kinds: set[str], where: str) -> None:
            if ids.get(ref) not in kinds:
                errors.append(f"{where}: {ref!r} is not a known {'/'.join(sorted(kinds))}")

        for p in self.ports:
            expect(p.region, {"Region"}, f"port {p.id}")
        for part in self.parts:
            for c in part.components:
                expect(c.part, {"Part"}, f"part {part.id} component")
        for prod in self.products:
            for c in prod.components:
                expect(c.part, {"Part"}, f"product {prod.id} component")
            for s in prod.customers:
                expect(s.customer, {"Customer"}, f"product {prod.id} customer")
        share_by_part: dict[str, float] = {}
        for sup in self.suppliers:
            expect(sup.region, {"Region"}, f"supplier {sup.id} region")
            for port in sup.ports:
                expect(port, {"Port"}, f"supplier {sup.id} port")
            for s in sup.supplies:
                expect(s.part, {"Part"}, f"supplier {sup.id} supplies")
                share_by_part[s.part] = share_by_part.get(s.part, 0.0) + s.share
        for part_id, total in share_by_part.items():
            if total > 1.0 + 1e-6:
                errors.append(f"part {part_id}: supplier shares sum to {total:.2f} > 1.0")
        for d in self.disruptions:
            for ref in d.affects:
                expect(ref, {"Region", "Port", "Supplier"}, f"disruption {d.id}")
            if d.expected_end and d.expected_end < d.started_at:
                errors.append(f"disruption {d.id}: expected_end before started_at")

        cycle = _find_bom_cycle(self.parts)
        if cycle:
            errors.append("bill of materials contains a cycle: " + " -> ".join(cycle))
        if errors:
            raise ValueError("network data failed integrity checks:\n  - " + "\n  - ".join(errors))
        return self


def _find_bom_cycle(parts: list[PartIn]) -> list[str] | None:
    graph = {p.id: [c.part for c in p.components] for p in parts}
    state: dict[str, int] = {}  # 1 = visiting, 2 = done
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt) :] + [nxt]
            if state.get(nxt) is None and (found := visit(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if node not in state and (found := visit(node)):
            return found
    return None


# --------------------------------------------------------------------------- tool contract


class Outcome(StrEnum):
    """Every tool answer is exactly one of these (see README, 'Honest outcomes')."""

    ANSWERED = "answered"  # the graph holds the relationships; paths are included
    FROM_MEMORY = "from_memory"  # a stored finding from an earlier session, with its date
    NOT_IN_GRAPH = "not_in_graph"  # the entity exists but no matching relationship does
    UNKNOWN_ENTITY = "unknown_entity"  # the name did not resolve; no traversal ran
    AMBIGUOUS = "ambiguous"  # several entities match; candidates are returned


class EntityRef(BaseModel):
    id: str
    name: str
    kind: str


class ToolResult(BaseModel):
    tool: str
    outcome: Outcome
    summary: str = Field(description="Deterministic one-paragraph summary built from the rows.")
    data: dict[str, Any] = Field(default_factory=dict)
    entities: list[EntityRef] = Field(
        default_factory=list, description="Every graph entity this result mentions."
    )
    paths: list[list[EntityRef]] = Field(
        default_factory=list, description="Graph paths backing the answer, upstream first."
    )
    candidates: list[EntityRef] = Field(default_factory=list)
    query_ms: float = 0.0
    rows: int = 0

    def for_model(self) -> dict[str, Any]:
        """Compact payload handed to the LLM (entities are implied by data/paths)."""
        payload = self.model_dump(mode="json", exclude={"entities", "query_ms"})
        if not payload["candidates"]:
            payload.pop("candidates")
        if not payload["paths"]:
            payload.pop("paths")
        return payload
