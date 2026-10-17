"""OpenAI adapter tests against a fake client shaped like the Responses API."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from supplygraph.config import Settings
from supplygraph.llm import LLMUnavailableError, OpenAIProvider, ToolDef, ToolOutput


class FakeResponses:
    def __init__(self, responses):
        self.queue = list(responses)
        self.create_calls: list[dict] = []
        self.parse_calls: list[dict] = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.queue.pop(0)

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return self.queue.pop(0)


def _client(*responses):
    return NS(responses=FakeResponses(responses))


def _usage():
    return NS(input_tokens=100, output_tokens=20, input_tokens_details=NS(cached_tokens=40))


def _response(output, status="completed", rid="resp_1", incomplete=None):
    return NS(id=rid, output=output, status=status, incomplete_details=incomplete, usage=_usage())


TOOLS = [
    ToolDef(name="find_entity", description="Find", parameters={"type": "object", "properties": {}})
]


@pytest.fixture
def settings():
    return Settings(_env_file=None, openai_model="gpt-test")


async def test_function_calls_and_chaining(settings):
    client = _client(
        _response(
            [
                NS(type="reasoning"),
                NS(
                    type="function_call",
                    call_id="c1",
                    name="find_entity",
                    arguments='{"query":"Busan"}',
                ),
                NS(type="function_call", call_id="c2", name="find_entity", arguments="not json"),
            ]
        ),
        _response(
            [NS(type="message", content=[NS(type="output_text", text="Answer.")])], rid="resp_2"
        ),
    )
    p = OpenAIProvider(settings, client=client)
    state = p.start("SYSTEM", [("user", "earlier")], "where is Busan?")
    turn = await p.step(state, TOOLS)
    assert turn.stop == "tool_use"
    assert turn.tool_calls[0].arguments == {"query": "Busan"}
    assert turn.tool_calls[1].error  # invalid JSON is surfaced, not executed
    assert turn.usage == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 40}

    first = client.responses.create_calls[0]
    assert first["model"] == "gpt-test" and first["instructions"] == "SYSTEM"
    assert first["input"][-1] == {"role": "user", "content": "where is Busan?"}
    assert first["previous_response_id"] is None
    assert first["tools"][0]["type"] == "function" and first["tools"][0]["name"] == "find_entity"

    p.add_tool_results(
        state, [ToolOutput("c1", "{}"), ToolOutput("c2", "Error: bad", is_error=True)]
    )
    turn = await p.step(state, TOOLS)
    second = client.responses.create_calls[1]
    assert second["previous_response_id"] == "resp_1"
    assert second["input"] == [
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": "Error: bad"},
    ]
    assert turn.stop == "end" and turn.text == "Answer."


async def test_refusal_and_truncation(settings):
    refusal = _response([NS(type="message", content=[NS(type="refusal", refusal="no")])])
    truncated = _response([], status="incomplete", incomplete=NS(reason="max_output_tokens"))
    p = OpenAIProvider(settings, client=_client(refusal, truncated))
    state = p.start("S", [], "q")
    assert (await p.step(state, TOOLS)).stop == "refusal"
    assert (await p.step(state, TOOLS)).stop == "max_tokens"


class Out(BaseModel):
    title: str


async def test_extract_pdf_sends_input_file(settings):
    client = _client(NS(output_parsed=Out(title="Strike"), status="completed"))
    p = OpenAIProvider(settings, client=client)
    out = await p.extract_pdf(data=b"%PDF-1.4 x", filename="a.pdf", prompt="Extract", schema=Out)
    assert out.title == "Strike"
    call = client.responses.parse_calls[0]
    part = call["input"][0]["content"][0]
    assert part["type"] == "input_file" and part["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert call["text_format"] is Out and call["store"] is False


async def test_extract_pdf_unreadable(settings):
    p = OpenAIProvider(settings, client=_client(NS(output_parsed=None, status="incomplete")))
    with pytest.raises(LLMUnavailableError):
        await p.extract_pdf(data=b"%PDF-", filename="a.pdf", prompt="x", schema=Out)
