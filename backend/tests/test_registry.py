import pytest

from supplygraph import tools as t
from supplygraph.registry import TOOLS, TOOLS_BY_NAME, ToolCallError, call_tool


def test_every_tool_has_a_method_and_object_schema():
    for spec in TOOLS:
        assert callable(getattr(t.SupplyGraphTools, spec.name))
        schema = spec.input_schema()
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        assert "title" not in schema


def test_only_record_finding_writes():
    assert [s.name for s in TOOLS if not s.read_only] == ["record_finding"]


async def test_rejects_unknown_tool():
    with pytest.raises(ToolCallError, match="unknown tool"):
        await call_tool(object(), "drop_database", {})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name,args",
    [
        ("disruption_impact", {"entity": "x", "max_depth": 99}),
        ("disruption_impact", {"entity": "x", "cypher": "MATCH (n) DETACH DELETE n"}),
        ("record_finding", {"entity": "x", "text": "short"}),
        ("supplier_criticality", {"limit": 0}),
    ],
)
async def test_rejects_out_of_bounds_arguments(name, args):
    with pytest.raises(ToolCallError, match="invalid arguments"):
        await call_tool(object(), name, args)  # type: ignore[arg-type]


def test_bound_guard():
    assert t._bound(3) == 3
    for bad in (0, 11, True, "3", 2.5):
        with pytest.raises(t.ToolInputError):
            t._bound(bad)  # type: ignore[arg-type]


def test_fulltext_query_is_sanitized():
    assert t.fulltext_query("Kaohsiung") == "Kaohsiung*"
    assert t.fulltext_query("port of @kaoh|siung -x") == "port* of kaoh* siung*"
    assert t.fulltext_query("@@ - !") is None
    # loose mode drops legal-form noise and ORs the rest
    assert t.fulltext_query("Nakdong Display Co., Ltd.", match_all=False) == "Nakdong*|Display*"


def test_tool_names_unique():
    assert len(TOOLS_BY_NAME) == len(TOOLS)


def test_names_overlap_needs_whole_words():
    assert t.names_overlap("Ayutthaya province", "Ayutthaya")
    assert t.names_overlap("Penang Microcontrollers Sdn. Bhd.", "Penang Microcontrollers")
    # a prefix match from full-text search is not a name match
    assert not t.names_overlap("New Port", "Port of Newcastle")
    assert not t.names_overlap("Co., Ltd.", "Nakdong Display")


def test_normalize_name_drops_noise():
    assert t.normalize_name("Ayutthaya Province") == "ayutthaya"
    assert t.normalize_name("Nakdong Display Co., Ltd.") == "nakdong display"
    assert t.normalize_name("Port of Suez") == "port suez"
    assert t.fulltext_query("Ayutthaya province", match_all=False) == "Ayutthaya*"
