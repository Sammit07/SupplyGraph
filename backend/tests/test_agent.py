"""Agent loop tests with a scripted LLM provider and stubbed tools (no network, no database)."""

from __future__ import annotations

from typing import Any

import pytest

from supplygraph import agent as agent_mod
from supplygraph.agent import SupplyGraphAgent
from supplygraph.config import Settings
from supplygraph.llm import LLMProvider, ModelTurn, ToolCall, ToolDef, ToolOutput
from supplygraph.models import EntityRef, Outcome, ToolResult
from supplygraph.registry import ToolCallError

EVX = EntityRef(id="PRD-EVX", name="Voltra EVX Sedan", kind="Product")
KCR = EntityRef(id="SUP-005", name="Katanga Cobalt Refining", kind="Supplier")


class ScriptedProvider(LLMProvider):
    name = "scripted"
    model = "scripted-1"

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.tool_results: list[list[ToolOutput]] = []
        self.started: tuple[str, list, str] | None = None
        self.tools_seen: list[ToolDef] = []

    def configured(self) -> bool:
        return True

    def start(self, system, history, question):
        self.started = (system, history, question)
        return {}

    async def step(self, state, tools):
        self.tools_seen = tools
        return self.turns.pop(0)

    def add_tool_results(self, state, results):
        self.tool_results.append(results)

    async def extract_pdf(self, **kwargs: Any):  # pragma: no cover - unused here
        raise NotImplementedError

    async def embed(self, texts):  # pragma: no cover - unused here
        raise NotImplementedError


def _tools(*calls: tuple[str, str, dict]) -> ModelTurn:
    return ModelTurn(
        text="",
        tool_calls=[ToolCall(id=i, name=n, arguments=a) for i, n, a in calls],
        stop="tool_use",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _end(text: str) -> ModelTurn:
    return ModelTurn(text=text, tool_calls=[], stop="end", usage={"input_tokens": 10})


class FakeCatalog:
    async def get(self, tenant):
        return [EVX, KCR]


@pytest.fixture
def stub_tools(monkeypatch):
    calls = []

    async def fake_call_tool(toolbox, name, arguments):
        calls.append((name, arguments))
        if name == "broken":
            raise ToolCallError("invalid arguments for broken: entity: field required")
        return ToolResult(
            tool=name,
            outcome=Outcome.ANSWERED,
            summary="Line-stop risk: Voltra EVX Sedan; revenue at risk $2.71B/yr.",
            data={"revenue_at_risk": 2_710_000_000, "revenue_at_risk_display": "$2.71B"},
            entities=[EVX, KCR],
            paths=[[KCR, EVX]],
            rows=3,
        )

    monkeypatch.setattr(agent_mod, "call_tool", fake_call_tool)
    return calls


def _agent(provider: LLMProvider) -> SupplyGraphAgent:
    settings = Settings(_env_file=None)
    return SupplyGraphAgent(settings, store=None, catalog=FakeCatalog(), provider=provider)  # type: ignore[arg-type]


async def test_tool_loop_and_grounded_answer(stub_tools):
    provider = ScriptedProvider(
        [
            _tools(
                ("t1", "disruption_impact", {"entity": "Katanga"}), ("t2", "active_disruptions", {})
            ),
            _end("Katanga Cobalt Refining halts the Voltra EVX Sedan; $2.71B at risk."),
        ]
    )
    answer = await _agent(provider).ask("what breaks?", tenant="demo")
    assert answer.grounded, answer.violations
    assert [t.tool for t in answer.tool_calls] == ["disruption_impact", "active_disruptions"]
    assert answer.paths and answer.paths[0][0].id == "SUP-005"
    # both results go back together, keyed by call id
    assert [r.call_id for r in provider.tool_results[0]] == ["t1", "t2"]
    assert answer.usage["input_tokens"] == 20
    assert answer.provider == "scripted"
    assert {t.name for t in provider.tools_seen} >= {"disruption_impact", "record_finding"}


async def test_stream_emits_tool_events_in_order(stub_tools):
    provider = ScriptedProvider([_tools(("t1", "active_disruptions", {})), _end("Done.")])
    events = [e["type"] async for e in _agent(provider).stream("?", tenant="demo")]
    assert events == ["start", "tool_start", "tool_end", "answer"]


async def test_ungrounded_answer_is_replaced(stub_tools):
    provider = ScriptedProvider(
        [_tools(("t1", "disruption_impact", {"entity": "Katanga"})), _end("About $9B is at risk.")]
    )
    answer = await _agent(provider).ask("what breaks?", tenant="demo")
    assert not answer.grounded
    assert "$9B" in answer.violations[0]
    assert answer.answer.startswith("Here is what the supply-chain graph returned")


async def test_tool_errors_are_returned_to_model(stub_tools):
    provider = ScriptedProvider([_tools(("t1", "broken", {})), _end("I could not run that query.")])
    answer = await _agent(provider).ask("?", tenant="demo")
    result = provider.tool_results[0][0]
    assert result.is_error and "field required" in result.content
    assert answer.tool_calls[0].error


async def test_unparseable_arguments_become_tool_errors(stub_tools):
    bad = ModelTurn(
        text="",
        tool_calls=[ToolCall(id="t1", name="find_entity", arguments={}, error="Expecting value")],
        stop="tool_use",
    )
    provider = ScriptedProvider([bad, _end("Sorry.")])
    await _agent(provider).ask("?", tenant="demo")
    assert provider.tool_results[0][0].is_error
    assert stub_tools == []  # the tool never ran


async def test_round_budget_exhaustion_falls_back(stub_tools):
    loop = [_tools((f"t{i}", "active_disruptions", {})) for i in range(8)]
    answer = await _agent(ScriptedProvider(loop)).ask("?", tenant="demo")
    assert not answer.grounded
    assert "stop=tool_use" in answer.violations[0]


async def test_refusal_is_handled(stub_tools):
    provider = ScriptedProvider([ModelTurn(text="", tool_calls=[], stop="refusal")])
    answer = await _agent(provider).ask("?", tenant="demo")
    assert answer.stop_reason == "refusal"
    assert "can't help" in answer.answer


async def test_history_is_passed_to_provider(stub_tools):
    provider = ScriptedProvider([_end("Ok.")])
    history = [
        agent_mod.Turn(role="user", content="hi"),
        agent_mod.Turn(role="assistant", content="hello"),
    ]
    await _agent(provider).ask("next?", tenant="demo", history=history)
    assert provider.started[1] == [("user", "hi"), ("assistant", "hello")]
    assert provider.started[2] == "next?"
