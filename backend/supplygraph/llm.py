"""LLM provider layer.

The agent and the PDF extractor talk to this interface, not to a vendor SDK:

* ``start`` / ``step`` / ``add_tool_results`` drive a tool-calling conversation. Each
  ``step`` returns a :class:`ModelTurn` with text, tool calls and a normalised stop reason.
* ``extract_pdf`` reads a PDF natively and returns a validated Pydantic object.

The shipped provider is OpenAI (Responses API, key from ``OPENAI_API_KEY``). Another vendor
can be added by implementing :class:`LLMProvider`. The model never writes queries.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from .config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
StopReason = Literal["tool_use", "end", "max_tokens", "refusal"]


class LLMUnavailableError(RuntimeError):
    """The LLM is not configured, unreachable, or rejected the request."""


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    error: str | None = None  # set when the model produced unparseable arguments


@dataclass(slots=True)
class ToolOutput:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(slots=True)
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop: StopReason
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def start(self, system: str, history: list[tuple[str, str]], question: str) -> Any:
        """Create provider-specific conversation state."""

    @abstractmethod
    async def step(self, state: Any, tools: list[ToolDef]) -> ModelTurn: ...

    @abstractmethod
    def add_tool_results(self, state: Any, results: list[ToolOutput]) -> None: ...

    @abstractmethod
    async def extract_pdf(
        self, *, data: bytes, filename: str, prompt: str, schema: type[T]
    ) -> T: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding vectors, one per input, in input order."""


# --------------------------------------------------------------------------- OpenAI

_EMBED_BATCH = 96


@dataclass(slots=True)
class _OpenAIState:
    system: str
    pending_input: list[dict[str, Any]]
    previous_response_id: str | None = None


class OpenAIProvider(LLMProvider):
    """OpenAI Responses API. Turns are chained with ``previous_response_id`` so reasoning
    state carries across tool rounds without resending the transcript."""

    name = "openai"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.model = settings.openai_model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(timeout=240.0, max_retries=2)
            except Exception as exc:
                raise LLMUnavailableError(f"OpenAI client not configured: {exc}") from exc
        return self._client

    def configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY")) or self._client is not None

    def start(self, system: str, history: list[tuple[str, str]], question: str) -> _OpenAIState:
        items = [{"role": role, "content": text} for role, text in history]
        items.append({"role": "user", "content": question})
        return _OpenAIState(system=system, pending_input=items)

    async def step(self, state: _OpenAIState, tools: list[ToolDef]) -> ModelTurn:
        try:
            response = await self.client.responses.create(
                model=self.model,
                instructions=state.system,
                input=state.pending_input,
                previous_response_id=state.previous_response_id,
                tools=[
                    {
                        "type": "function",
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        "strict": False,
                    }
                    for t in tools
                ],
                reasoning={"effort": self.settings.llm_effort},
                max_output_tokens=self.settings.llm_max_output_tokens,
            )
        except Exception as exc:
            raise _translate_openai(exc) from exc
        state.previous_response_id = response.id
        state.pending_input = []

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        refused = False
        for item in response.output or []:
            if item.type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    calls.append(ToolCall(id=item.call_id, name=item.name, arguments=args))
                except ValueError as exc:
                    calls.append(
                        ToolCall(id=item.call_id, name=item.name, arguments={}, error=str(exc))
                    )
            elif item.type == "message":
                for part in item.content or []:
                    if part.type == "output_text":
                        text_parts.append(part.text)
                    elif part.type == "refusal":
                        refused = True

        incomplete = getattr(response, "incomplete_details", None)
        if calls:
            stop: StopReason = "tool_use"
        elif refused:
            stop = "refusal"
        elif (
            response.status == "incomplete"
            and incomplete
            and incomplete.reason == "max_output_tokens"
        ):
            stop = "max_tokens"
        elif response.status == "incomplete":
            stop = (
                "refusal" if incomplete and incomplete.reason == "content_filter" else "max_tokens"
            )
        else:
            stop = "end"
        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    response.usage.input_tokens_details, "cached_tokens", 0
                ),
            }
        return ModelTurn(text="".join(text_parts).strip(), tool_calls=calls, stop=stop, usage=usage)

    def add_tool_results(self, state: _OpenAIState, results: list[ToolOutput]) -> None:
        state.pending_input = [
            {"type": "function_call_output", "call_id": r.call_id, "output": r.content}
            for r in results
        ]

    async def extract_pdf(self, *, data: bytes, filename: str, prompt: str, schema: type[T]) -> T:
        encoded = base64.standard_b64encode(data).decode("ascii")
        try:
            response = await self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": filename,
                                "file_data": f"data:application/pdf;base64,{encoded}",
                            },
                            {"type": "input_text", "text": prompt},
                        ],
                    }
                ],
                text_format=schema,
                reasoning={"effort": self.settings.llm_extraction_effort},
                max_output_tokens=self.settings.llm_max_output_tokens,
                store=False,
            )
        except Exception as exc:
            raise _translate_openai(exc) from exc
        parsed = response.output_parsed
        if parsed is None:
            raise LLMUnavailableError(f"the document could not be read (status={response.status})")
        return parsed

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH):
            batch = texts[start : start + _EMBED_BATCH]
            try:
                response = await self.client.embeddings.create(
                    model=self.settings.openai_embedding_model,
                    input=batch,
                    dimensions=self.settings.embedding_dimensions,
                )
            except Exception as exc:
                raise _translate_openai(exc) from exc
            ordered = sorted(response.data, key=lambda d: d.index)
            vectors.extend(list(d.embedding) for d in ordered)
        return vectors


def _translate_openai(exc: Exception) -> LLMUnavailableError:
    try:
        import openai
    except ImportError:  # pragma: no cover
        return LLMUnavailableError(str(exc))
    if isinstance(exc, LLMUnavailableError):
        return exc
    if isinstance(exc, openai.AuthenticationError):
        return LLMUnavailableError("OpenAI rejected the API key")
    if isinstance(exc, openai.RateLimitError):
        return LLMUnavailableError("LLM rate limit or quota reached; retry shortly")
    if isinstance(exc, openai.BadRequestError):
        log.error("openai bad request", extra={"error": str(exc)[:500]})
        return LLMUnavailableError("the LLM rejected the request (check the model name)")
    if isinstance(exc, openai.APIStatusError):
        return LLMUnavailableError(f"LLM request failed ({exc.status_code})")
    if isinstance(exc, openai.APIConnectionError):
        return LLMUnavailableError("LLM provider unreachable")
    return LLMUnavailableError(str(exc))


# --------------------------------------------------------------------------- factory


def build_provider(settings: Settings) -> LLMProvider:
    """Create the configured provider. Build once per process and reuse it."""
    return OpenAIProvider(settings)
