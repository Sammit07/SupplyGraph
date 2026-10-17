from supplygraph.guardrails import check_answer, fallback_answer
from supplygraph.models import EntityRef, Outcome, ToolResult

CATALOG = [
    EntityRef(id="REG-JP-KYU", name="Kyushu", kind="Region"),
    EntityRef(id="SUP-011", name="Kyushu Membrane Co.", kind="Supplier"),
    EntityRef(id="SUP-005", name="Katanga Cobalt Refining", kind="Supplier"),
    EntityRef(id="PRD-EVX", name="Voltra EVX Sedan", kind="Product"),
]


def _result(**kw) -> ToolResult:
    base = dict(tool="disruption_impact", outcome=Outcome.ANSWERED, summary="s")
    return ToolResult(**(base | kw))


def test_accepts_grounded_answer():
    r = _result(
        entities=[CATALOG[2], CATALOG[3]],
        data={"revenue_at_risk_usd": 2_710_000_000, "revenue_at_risk": "$2.71B"},
    )
    report = check_answer(
        "Katanga Cobalt Refining is down, so the Voltra EVX Sedan has $2.71B at risk.",
        [r],
        CATALOG,
    )
    assert report.grounded, report.violations


def test_flags_entity_without_evidence():
    r = _result(entities=[CATALOG[3]])
    report = check_answer("Switch to Katanga Cobalt Refining.", [r], CATALOG)
    assert not report.grounded
    assert "Katanga Cobalt Refining" in report.violations[0]


def test_longest_name_wins():
    # "Kyushu Membrane Co." must not also count as the region "Kyushu".
    r = _result(entities=[CATALOG[1]])
    assert check_answer("Kyushu Membrane Co. is the only separator source.", [r], CATALOG).grounded


def test_flags_invented_figure():
    r = _result(entities=[CATALOG[3]], data={"revenue_at_risk_usd": 2_710_000_000})
    report = check_answer("Voltra EVX Sedan risks $3.5B.", [r], CATALOG)
    assert not report.grounded
    assert "$3.5B" in report.violations[0]


def test_figure_precision_tolerance():
    r = _result(entities=[], data={"x": 2_495_000_000})
    assert check_answer("About $2.5 billion is at risk.", [r], CATALOG).grounded
    assert check_answer("$2,495,000,000 at risk.", [r], CATALOG).grounded
    assert not check_answer("$2.3B at risk.", [r], CATALOG).grounded


def test_figures_without_any_data_are_rejected():
    r = ToolResult(tool="disruption_impact", outcome=Outcome.UNKNOWN_ENTITY, summary="none")
    assert not check_answer("Roughly $1M is at risk.", [r], CATALOG).grounded


def test_fallback_uses_only_summaries():
    r = _result(summary="Line-stop risk for Voltra EVX Sedan.")
    text = fallback_answer([r, r])
    assert text.count("Line-stop risk") == 1
    assert "no graph query" in fallback_answer([])


def test_names_from_tool_text_or_question_are_grounded():
    penang = EntityRef(id="REG-MY-PEN", name="Penang", kind="Region")
    catalog = [*CATALOG, penang]
    r = _result(entities=[CATALOG[2]], summary="Shipments from the Penang site are suspended.")
    # appears in tool text
    assert check_answer("The Penang site is down.", [r], catalog).grounded
    # appears only in the user's question
    r2 = _result(entities=[CATALOG[2]], summary="ok")
    assert check_answer(
        "Penang is affected.", [r2], catalog, question="What about Penang?"
    ).grounded
    # appears nowhere: still a violation
    assert not check_answer("Penang is affected.", [r2], catalog).grounded
