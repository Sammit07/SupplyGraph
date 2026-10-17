"""The agent loop: the LLM selects tools and phrases results; FalkorDB does the reasoning.

Flow for one question:
    1. The model picks tools and typed arguments (it never sees or writes Cypher).
    2. Tools resolve names and run fixed traversals; results carry an explicit outcome.
    3. The loop repeats until the model answers or the round budget runs out.
    4. The answer is checked against the tool evidence; on failure it is replaced by a
       summary built from the results themselves.

``stream`` yields progress events (tool start/end, final answer) for server-sent events;
``ask`` runs the same loop and returns only the final answer. The loop is provider-neutral
(see :mod:`supplygraph.llm`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from .catalog import CatalogCache
from .config import Settings
from .db import GraphStore
from .guardrails import check_answer, fallback_answer
from .llm import LLMProvider, ToolCall, ToolDef, ToolOutput, build_provider
from .models import EntityRef, ToolResult
from .registry import TOOLS, ToolCallError, call_tool
from .tools import SupplyGraphTools, toolbox_for

log = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 40_000

SYSTEM_PROMPT = """\
You are SupplyGraph, a supply-chain risk analyst for a manufacturer. Every fact you use comes \
from tools that traverse a graph of the company's suppliers, parts, bill of materials, \
products, customers, regions, ports and disruptions.

How to work:
- Call tools for every factual claim. Do not name a supplier, part, product, customer, region, \
port, figure or date unless it appeared in a tool result in this conversation.
- Pass names as the user wrote them; the tools resolve names against the graph. When a tool \
returns `ambiguous`, call again with the id of the candidate the user clearly meant, or ask \
the user which one they meant if it is not clear.
- Each tool result has an `outcome`:
  - `answered`: the graph holds the relationships; cite the path (`critical_path`, hops).
  - `not_in_graph`: the entity exists but the graph has no such relationship. Say exactly \
that. It does not mean "no impact" or "safe".
  - `unknown_entity`: the name is not in the graph; no query ran. Say so.
  - `from_memory`: a finding from an earlier session; give its date.
- Quote money figures exactly as tools formatted them (the `*_display` fields). Do not add, \
estimate or convert figures yourself.
- Use several tools when the question needs it, e.g. impact first, then alternate suppliers \
for the parts that are blocked.
- `disruption_impact` analyses one event on its own. If its result has `compounded_parts` \
(or a WARNING about other active disruptions), or an alternate supplier is itself disrupted, \
also call `combined_impact` and lead with the combined result: an event that only \
"constrains" on its own can still stop production when combined with others.
- Record a finding only when the user asks you to remember something, or when you reached a \
durable, non-obvious conclusion that the tool results directly support.

Answer style: lead with the direct answer in one or two sentences, then a few short bullets \
with the evidence (paths, parts, alternates). Stay under 200 words unless asked for detail. \
Plain text and simple dashes for bullets; no tables or headings."""

REFUSAL_TEXT = (
    "I can't help with that request. Ask about suppliers, parts, products, disruptions or "
    "exposure in the supply-chain graph."
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ToolTrace(BaseModel):
    tool: str
    arguments: dict[str, Any]
    outcome: str | None = None
    rows: int = 0
    elapsed_ms: float = 0.0
    summary: str | None = None
    error: str | None = None


class AgentAnswer(BaseModel):
    answer: str
    grounded: bool
    violations: list[str] = Field(default_factory=list)
    session_id: str
    tenant: str
    tool_calls: list[ToolTrace] = Field(default_factory=list)
    paths: list[list[EntityRef]] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    stop_reason: str | None = None
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_ms: float = 0.0


@dataclass(slots=True)
class _TurnState:
    results: list[ToolResult] = field(default_factory=list)
    traces: list[ToolTrace] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class SupplyGraphAgent:
    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        catalog: CatalogCache,
        provider: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.catalog = catalog
        self.provider = provider or build_provider(settings)
        self._tool_defs = [
            ToolDef(name=s.name, description=s.description, parameters=s.input_schema())
            for s in TOOLS
        ]

    async def ask(
        self,
        question: str,
        *,
        tenant: str,
        session_id: str | None = None,
        history: list[Turn] | None = None,
    ) -> AgentAnswer:
        final: AgentAnswer | None = None
        async for event in self.stream(
            question, tenant=tenant, session_id=session_id, history=history
        ):
            if event["type"] == "answer":
                final = AgentAnswer.model_validate(event["answer"])
        assert final is not None
        return final

    async def stream(
        self,
        question: str,
        *,
        tenant: str,
        session_id: str | None = None,
        history: list[Turn] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        session_id = session_id or uuid.uuid4().hex
        toolbox = toolbox_for(
            self.settings, self.store, tenant, provider=self.provider, session_id=session_id
        )
        state = _TurnState()
        conversation = self.provider.start(
            SYSTEM_PROMPT, [(t.role, t.content) for t in (history or [])[-10:]], question
        )
        yield {"type": "start", "session_id": session_id}

        final_text: str | None = None
        stop: str | None = None
        for _ in range(self.settings.agent_max_tool_rounds):
            turn = await self.provider.step(conversation, self._tool_defs)
            for key, value in turn.usage.items():
                state.usage[key] = state.usage.get(key, 0) + value
            stop = turn.stop
            if stop == "refusal":
                final_text = REFUSAL_TEXT
                break
            if stop == "tool_use":
                for c in turn.tool_calls:
                    yield {
                        "type": "tool_start",
                        "id": c.id,
                        "tool": c.name,
                        "arguments": c.arguments,
                    }
                done = await asyncio.gather(
                    *(self._run(toolbox, c, state) for c in turn.tool_calls)
                )
                for c, (_, trace) in zip(turn.tool_calls, done, strict=True):
                    yield {"type": "tool_end", "id": c.id, **trace.model_dump()}
                self.provider.add_tool_results(conversation, [out for out, _ in done])
                continue
            final_text = turn.text
            break

        grounded, violations = True, []
        if stop == "refusal":
            pass
        elif not final_text or stop in ("max_tokens", "tool_use"):
            grounded, violations = False, [f"no complete answer (stop={stop})"]
            final_text = fallback_answer(state.results)
        else:
            report = check_answer(
                final_text, state.results, await self.catalog.get(tenant), question
            )
            if not report.grounded:
                log.warning(
                    "answer failed grounding",
                    extra={"tenant": tenant, "violations": report.violations},
                )
                grounded, violations = False, report.violations
                final_text = fallback_answer(state.results)

        entities: dict[str, EntityRef] = {}
        for r in state.results:
            for e in r.entities:
                entities[e.id] = e
        answer = AgentAnswer(
            answer=final_text,
            grounded=grounded,
            violations=violations,
            session_id=session_id,
            tenant=tenant,
            tool_calls=state.traces,
            paths=_dedupe_paths(p for r in state.results for p in r.paths),
            entities=list(entities.values())[:200],
            stop_reason=stop,
            provider=self.provider.name,
            model=self.provider.model,
            usage=state.usage,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        yield {"type": "answer", "answer": answer.model_dump(mode="json")}

    async def _run(
        self, toolbox: SupplyGraphTools, call: ToolCall, state: _TurnState
    ) -> tuple[ToolOutput, ToolTrace]:
        trace = ToolTrace(tool=call.name, arguments=call.arguments)
        start = time.perf_counter()
        try:
            if call.error:
                raise ToolCallError(f"could not parse arguments: {call.error}")
            result = await call_tool(toolbox, call.name, call.arguments)
        except ToolCallError as exc:
            trace.error = str(exc)
            trace.elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            state.traces.append(trace)
            return ToolOutput(call.id, f"Error: {exc}", is_error=True), trace
        trace.outcome = result.outcome.value
        trace.rows = result.rows
        trace.summary = result.summary
        trace.elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        state.traces.append(trace)
        state.results.append(result)
        content = json.dumps(result.for_model(), default=str, separators=(",", ":"))
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = json.dumps(
                {
                    "tool": result.tool,
                    "outcome": result.outcome.value,
                    "summary": result.summary,
                    "note": "full result too large; summary only",
                }
            )
        return ToolOutput(call.id, content), trace


def _dedupe_paths(paths) -> list[list[EntityRef]]:
    seen: set[tuple[str, ...]] = set()
    out = []
    for p in paths:
        key = tuple(e.id for e in p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
