"""Single source of truth for tool definitions, shared by the agent, REST API and MCP server."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from . import tools as t
from .db import GraphQueryError, GraphUnavailableError
from .llm import LLMUnavailableError
from .models import ToolResult

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    read_only: bool = True

    def input_schema(self) -> dict[str, Any]:
        return _clean_schema(self.input_model.model_json_schema())


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "find_entity",
        "Look up suppliers, parts, products, customers, regions, ports or disruptions by name "
        "or id. Use it when a name is vague or a previous tool returned 'ambiguous'.",
        t.FindEntityIn,
    ),
    ToolSpec(
        "active_disruptions",
        "List current disruptions (weather, geopolitical, labor...) and what each one affects. "
        "Start here for 'what is going on' or 'what should I worry about' questions.",
        t.ActiveDisruptionsIn,
    ),
    ToolSpec(
        "disruption_impact",
        "Blast radius of an outage. Starting from a disruption, region, port, supplier or part, "
        "finds the suppliers knocked out (located there, or every export port blocked), the "
        "parts that lose supply and what sourcing remains, then walks the bill of materials "
        "up to finished products and customers. Returns line-stop risk, days of inventory "
        "cover, revenue at risk and the critical path for each product.",
        t.DisruptionImpactIn,
    ),
    ToolSpec(
        "combined_impact",
        "Impact of ALL active disruptions together. Losses compound: a part whose suppliers are "
        "hit by different disruptions can lose all its supply even when no single disruption "
        "blocks it. Use for overall exposure or 'what is at risk right now' questions.",
        t.CombinedImpactIn,
    ),
    ToolSpec(
        "upstream_exposure",
        "Walk down from a product or customer to every supplier at every tier: country "
        "concentration, suppliers per tier and which deep-tier suppliers are under an active "
        "disruption.",
        t.UpstreamExposureIn,
    ),
    ToolSpec(
        "single_points_of_failure",
        "Parts in the bill of materials that are single-sourced or sourced from one country "
        "only, ranked by downstream revenue. Optionally limited to one product.",
        t.SinglePointsIn,
    ),
    ToolSpec(
        "alternate_suppliers",
        "All qualified suppliers of a part with share, lead time, cost, country and whether "
        "each is currently disrupted, degraded or available.",
        t.AlternateSuppliersIn,
    ),
    ToolSpec(
        "trace_connection",
        "Shortest supply-chain path between any two entities, e.g. how a region connects to "
        "a product or customer. Returns each hop with its relationship.",
        t.TraceConnectionIn,
    ),
    ToolSpec(
        "critical_lead_time",
        "The longest sourcing chain (sum of supplier lead times from raw material to the "
        "finished product) for a product, with primary-supplier and best-case totals.",
        t.CriticalLeadTimeIn,
    ),
    ToolSpec(
        "supplier_criticality",
        "Rank suppliers by the revenue that depends on them alone (sole-sourced parts) and "
        "their total downstream revenue reach.",
        t.SupplierCriticalityIn,
    ),
    ToolSpec(
        "search_documents",
        "Semantic search over ingested risk documents (supplier notices, advisories, "
        "bulletins). Returns the most relevant passages with their source document and page, "
        "the disruptions that document reported, and the suppliers, ports and regions each "
        "passage mentions. Use it to quote what a document actually says.",
        t.SearchDocumentsIn,
    ),
    ToolSpec(
        "recall_findings",
        "Read findings stored in earlier sessions about an entity, newest first, with dates.",
        t.RecallFindingsIn,
    ),
    ToolSpec(
        "record_finding",
        "Store one durable, evidence-backed finding about an entity for future sessions. Only "
        "record conclusions that tool results in this conversation directly support.",
        t.RecordFindingIn,
        read_only=False,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


class ToolCallError(Exception):
    """A tool call failed in a way the caller (model or API client) should see."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


async def call_tool(
    toolbox: t.SupplyGraphTools, name: str, arguments: dict[str, Any] | None
) -> ToolResult:
    """Validate arguments and run a tool. Raises ToolCallError with a model-readable message."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ToolCallError(f"unknown tool '{name}'. Available: {', '.join(TOOLS_BY_NAME)}")
    try:
        args = spec.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
        )
        raise ToolCallError(f"invalid arguments for {name}: {problems}") from exc

    start = time.perf_counter()
    timings = t.start_query_timer()
    try:
        result: ToolResult = await getattr(toolbox, name)(args)
    except t.ToolInputError as exc:
        raise ToolCallError(str(exc)) from exc
    except GraphUnavailableError as exc:
        raise ToolCallError(f"graph database unavailable: {exc}", retryable=True) from exc
    except LLMUnavailableError as exc:
        raise ToolCallError(f"language model unavailable: {exc}", retryable=True) from exc
    except GraphQueryError as exc:
        log.exception("tool query failed", extra={"tool": name})
        raise ToolCallError(f"{name} failed on the graph side; this is a server bug") from exc
    elapsed = (time.perf_counter() - start) * 1000
    # Report the total graph time of every query the tool ran, on every outcome.
    result.query_ms = round(sum(timings), 1)
    log.info(
        "tool call",
        extra={
            "tool": name,
            "tenant": toolbox.tenant,
            "tool_args": args.model_dump(exclude_none=True),
            "outcome": result.outcome.value,
            "rows": result.rows,
            "elapsed_ms": round(elapsed, 1),
        },
    )
    return result


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop Pydantic noise (titles) and inline nothing else; keeps the schema small."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned
