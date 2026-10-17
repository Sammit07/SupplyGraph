"""Post-hoc grounding check on the model's final answer.

The model only phrases results; it must not introduce facts. Two checks run on every
answer before it reaches the user:

1. Entities: any graph entity named in the answer must appear in this turn's tool results.
   Names are matched longest-first against the tenant's full entity catalog, so "Kyushu
   Membrane Co." does not also count as a mention of the region "Kyushu".
2. Money: every currency figure must match a number in the tool results, within the
   precision the answer wrote it at.

If either check fails, the caller replaces the answer with one built from tool summaries.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import EntityRef, Outcome, ToolResult

# A currency symbol or ISO 4217 code, then an amount with an optional magnitude suffix.
_MONEY_RE = re.compile(
    r"(?:[$€£¥₹]|\b[A-Z]{3}\s)\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?\s*"
    r"(?P<unit>billion|million|thousand|bn|B|M|K|k)?\b",
)
_UNIT = {"b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}
_MIN_NAME_LEN = 4


@dataclass(slots=True)
class GroundingReport:
    grounded: bool
    violations: list[str] = field(default_factory=list)


def _numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield float(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _numbers(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _numbers(v)
    elif isinstance(value, str):
        for m in _MONEY_RE.finditer(value):
            yield _money_value(m)[0]


def _money_value(m: re.Match[str]) -> tuple[float, float]:
    """Return (value, tolerance) for a currency match."""
    whole = float(m["num"].replace(",", ""))
    dec = m["dec"] or ""
    mult = _UNIT.get((m["unit"] or "").lower(), 1.0)
    value = (whole + (float(f"0.{dec}") if dec else 0.0)) * mult
    # half a unit in the last written digit, at least 1%
    precision = 0.5 * (10 ** -len(dec)) * mult
    return value, max(precision, abs(value) * 0.01)


def check_answer(
    answer: str, results: list[ToolResult], catalog: list[EntityRef], question: str = ""
) -> GroundingReport:
    violations: list[str] = []
    evidence_ids = {e.id for r in results for e in (*r.entities, *r.candidates)}
    for r in results:
        for path in r.paths:
            evidence_ids.update(e.id for e in path)
    # Words the model may repeat without a registered entity: anything the tools wrote
    # (summaries, data, entity names) and the user's own question. A name found nowhere in
    # these is what the check exists to catch.
    evidence_text = " ".join(
        [question]
        + [r.summary for r in results]
        + [json.dumps(r.data, default=str) for r in results]
        + [e.name for r in results for e in (*r.entities, *r.candidates)]
    ).lower()

    # 1. entity mentions, longest names first, consuming matched spans
    text = answer.lower()
    for entity in sorted(catalog, key=lambda e: -len(e.name)):
        name = entity.name.lower()
        if len(name) < _MIN_NAME_LEN:
            continue
        pattern = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])")
        if not pattern.search(text):
            continue
        text = pattern.sub(" ", text)
        if entity.id not in evidence_ids and not pattern.search(evidence_text):
            violations.append(f"mentions {entity.name} ({entity.kind}) without tool evidence")

    # 2. currency figures
    evidence_numbers = [n for r in results for n in _numbers(r.data)]
    evidence_numbers += [n for r in results for n in _numbers(r.summary)]
    for m in _MONEY_RE.finditer(answer):
        value, tol = _money_value(m)
        if not any(abs(value - n) <= tol for n in evidence_numbers):
            violations.append(f"figure {m.group(0).strip()} does not appear in tool results")

    # 3. impact claims with no supporting traversal
    if results and not any(r.outcome in (Outcome.ANSWERED, Outcome.FROM_MEMORY) for r in results):
        if _MONEY_RE.search(answer):
            violations.append("quotes figures although no tool returned data")

    return GroundingReport(grounded=not violations, violations=violations)


def fallback_answer(results: list[ToolResult]) -> str:
    """An answer assembled only from tool output, used when the drafted answer fails checks."""
    if not results:
        return (
            "I could not answer this from the supply-chain graph: no graph query was run. "
            "Try naming a supplier, part, product, region, port or disruption."
        )
    lines = ["Here is what the supply-chain graph returned:"]
    seen: set[str] = set()
    for r in results:
        if r.summary not in seen and r.tool != "record_finding":
            seen.add(r.summary)
            lines.append(f"- {r.summary}")
    return "\n".join(lines)
