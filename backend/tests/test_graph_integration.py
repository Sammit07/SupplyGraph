"""End-to-end tool tests against a real FalkorDB, seeded with the bundled network.

Expected values are derived by hand from data/seed.json; each assertion is a supply-chain
conclusion that only exists because of multi-hop traversal.
"""

import pytest
from conftest import SAMPLE_CSV

from supplygraph import tools as t
from supplygraph.models import Outcome

pytestmark = pytest.mark.integration


async def test_resolution_exact_ambiguous_unknown(tools):
    res = await tools.resolve("Kaohsiung Silicon Foundry")
    assert res.entity and res.entity.id == "SUP-014"
    res = await tools.resolve("SUP-014")
    assert res.entity and res.entity.id == "SUP-014"
    loose = await tools.resolve("Nakdong Display Co., Ltd.", ["Supplier"])
    assert loose.entity and loose.entity.id == "SUP-033"

    amb = await tools.find_entity(t.FindEntityIn(query="Kyushu"))
    assert {c.id for c in amb.entities} >= {"REG-JP-KYU", "SUP-011", "SUP-019"}

    assert amb.entities[0].id == "REG-JP-KYU"  # exact name match first

    # an exact name resolves; a shared prefix does not
    region = await tools.resolve("Kyushu")
    assert region.entity and region.entity.id == "REG-JP-KYU"
    impact = await tools.disruption_impact(t.DisruptionImpactIn(entity="Monterrey"))
    assert impact.outcome == Outcome.AMBIGUOUS
    assert {c.id for c in impact.candidates} >= {"SUP-020", "SUP-037"}

    unknown = await tools.disruption_impact(t.DisruptionImpactIn(entity="Atlantis Gigafactory"))
    assert unknown.outcome == Outcome.UNKNOWN_ENTITY


async def test_katanga_cobalt_stops_battery_products(tools):
    r = await tools.disruption_impact(t.DisruptionImpactIn(entity="DIS-2026-KATANGA-EXPORT"))
    assert r.outcome == Outcome.ANSWERED
    assert [s["id"] for s in r.data["suppliers_hit"]] == ["SUP-005"]
    cobalt = next(p for p in r.data["parts"] if p["id"] == "PRT-COSO4")
    assert cobalt["share_lost"] == 1.0 and cobalt["unaffected_alternates"] == []

    products = {p["id"]: p for p in r.data["products"]}
    assert set(products) == {"PRD-EVX", "PRD-PACK75"}
    assert all(p["status"] == "line_stop_risk" for p in products.values())
    assert r.data["revenue_at_risk"] == 2_710_000_000
    # cover: cobalt 25 + cathode 20 + cell 14 + module 7 + pack 5 = 71 days < 107-day outage
    assert r.data["outage_days"] == 107
    assert products["PRD-EVX"]["days_of_cover"] == 71
    assert products["PRD-EVX"]["stockout_expected"] is True
    # the path explains the answer, tier 5 supplier up to the car
    path = [e.id for e in r.paths[0]]
    assert path[0] == "SUP-005" and path[-1] in {"PRD-EVX", "PRD-PACK75"}


async def test_typhoon_distinguishes_blocked_constrained_and_degraded(tools):
    r = await tools.disruption_impact(
        t.DisruptionImpactIn(entity="Typhoon Haikui", kind="Disruption")
    )
    hit = {s["id"]: s["reason"] for s in r.data["suppliers_hit"]}
    assert hit == {"SUP-014": "site_in_affected_region", "SUP-016": "site_in_affected_region"}
    # Hsinchu SiC exports through Kaohsiung *and* Keelung: degraded, not knocked out
    assert [s["id"] for s in r.data["suppliers_degraded"]] == ["SUP-031"]

    status = {p["id"]: p["status"] for p in r.data["products"]}
    assert status["PRD-EVX"] == "line_stop_risk"  # SoC is sole-sourced in Kaohsiung
    assert status["PRD-INFO"] == "line_stop_risk"
    assert status["PRD-HUB"] == "constrained"  # MCU keeps Penang's 20%
    assert status["PRD-PACK75"] == "constrained"
    assert r.data["revenue_at_risk"] == 2_495_000_000


async def test_port_closure_without_full_blockage_is_constrained(tools):
    r = await tools.disruption_impact(t.DisruptionImpactIn(entity="Port of Odesa"))
    neon = next(p for p in r.data["parts"] if p["id"] == "PRT-NEON")
    assert neon["share_lost"] == 0.65
    assert all(p["status"] == "constrained" for p in r.data["products"])
    assert r.data["revenue_at_risk"] == 0


async def test_entity_with_no_supply_relationships_is_not_in_graph(tools):
    r = await tools.disruption_impact(t.DisruptionImpactIn(entity="South Holland", kind="Region"))
    assert r.outcome == Outcome.NOT_IN_GRAPH
    assert "no supplier" in r.summary


async def test_part_outage_and_depth_bound(tools):
    deep = await tools.disruption_impact(
        t.DisruptionImpactIn(entity="Purified Neon Gas", kind="Part")
    )
    assert {p["id"] for p in deep.data["products"]} == {
        "PRD-EVX",
        "PRD-PACK75",
        "PRD-HUB",
        "PRD-INFO",
    }
    shallow = await tools.disruption_impact(
        t.DisruptionImpactIn(entity="Purified Neon Gas", kind="Part", max_depth=2)
    )
    assert shallow.outcome == Outcome.NOT_IN_GRAPH  # neon is >= 4 hops from any product


async def test_single_points_of_failure_for_evx(tools):
    r = await tools.single_points_of_failure(t.SinglePointsIn(product="Voltra EVX Sedan", limit=50))
    risk = {p["id"]: p["risk"] for p in r.data["parts"]}
    assert risk["PRT-COSO4"] == "single_source"
    assert risk["PRT-SOC"] == "single_source"
    assert risk["PRT-GRAPH"] == "single_source"
    assert "PRT-CELL" not in risk  # Korea + China


async def test_alternate_suppliers_reflect_live_disruptions(tools):
    r = await tools.alternate_suppliers(
        t.AlternateSuppliersIn(part="Automotive 32-bit Microcontroller")
    )
    status = {s["id"]: s["status"] for s in r.data["suppliers"]}
    assert status == {"SUP-015": "available", "SUP-014": "disrupted"}
    sic = await tools.alternate_suppliers(t.AlternateSuppliersIn(part="SiC Power MOSFET"))
    assert {s["id"]: s["status"] for s in sic.data["suppliers"]}["SUP-031"] == "degraded"


async def test_upstream_exposure_finds_deep_tier_disruption(tools):
    r = await tools.upstream_exposure(t.UpstreamExposureIn(entity="Voltra Service Centers"))
    disrupted = {s["id"]: s["tier"] for s in r.data["disrupted_suppliers"]}
    assert disrupted["SUP-005"] == 5  # cobalt is five BOM hops below the service pack
    assert "SUP-014" in disrupted


async def test_trace_connection_region_to_product(tools):
    r = await tools.trace_connection(
        t.TraceConnectionIn(source="Odesa Oblast", target="Voltra Home Charging Hub")
    )
    assert r.outcome == Outcome.ANSWERED
    ids = [e.id for e in r.paths[0]]
    assert ids[0] == "REG-UA-OD" and ids[-1] == "PRD-HUB"
    assert "SUP-018" in ids


async def test_critical_lead_time_evx(tools):
    r = await tools.critical_lead_time(t.CriticalLeadTimeIn(product="PRD-EVX"))
    # neon 45 + wafer 60 + MCU 110 + inverter 40 + drive unit 21
    assert r.data["chains"][0]["total_days"] == 276


async def test_supplier_criticality_ranks_hidden_chokepoint_first(tools):
    r = await tools.supplier_criticality(t.SupplierCriticalityIn(limit=5))
    top = r.data["suppliers"][0]
    # copper foil feeds every PCB, which feeds all four products
    assert top["id"] == "SUP-022"
    assert top["sole_source_revenue"] == 2_985_000_000


async def test_memory_round_trip(tools):
    empty = await tools.recall_findings(t.RecallFindingsIn(entity="SUP-027"))
    assert empty.outcome == Outcome.NOT_IN_GRAPH
    await tools.record_finding(
        t.RecordFindingIn(
            entity="Ningbo Magnetics", text="Sole NdFeB magnet source; qualify a second."
        )
    )
    mem = await tools.recall_findings(t.RecallFindingsIn(entity="SUP-027"))
    assert mem.outcome == Outcome.FROM_MEMORY
    assert "qualify a second" in mem.data["findings"][0]["text"]


async def test_recall_follows_affects_one_hop(tools):
    # A finding about a disruption is recalled when asking about the port it affects.
    await tools.record_finding(
        t.RecordFindingIn(entity="DIS-2026-TYPHOON-KHH", text="Typhoon closure: expedite via air.")
    )
    mem = await tools.recall_findings(t.RecallFindingsIn(entity="PORT-KHH"))
    assert mem.outcome == Outcome.FROM_MEMORY
    hit = next(f for f in mem.data["findings"] if "expedite via air" in f["text"])
    assert hit["about"]["id"] == "DIS-2026-TYPHOON-KHH"
    assert "(about Typhoon Haikui" in mem.summary
    assert "DIS-2026-TYPHOON-KHH" in {e.id for e in mem.entities}


async def test_active_disruptions_excludes_inactive(tools):
    r = await tools.active_disruptions(t.ActiveDisruptionsIn())
    ids = {d["id"] for d in r.data["disruptions"]}
    assert "DIS-2025-RTM-STRIKE" not in ids and len(ids) == 3
    everything = await tools.active_disruptions(t.ActiveDisruptionsIn(include_inactive=True))
    assert len(everything.data["disruptions"]) == 4


async def test_ingest_is_idempotent(store, tenant):
    from supplygraph.ingest import ingest, load_dataset_dir

    before = await store.read(tenant, "MATCH (n:Entity) RETURN count(n) AS n", op="t")
    await ingest(store, tenant, load_dataset_dir(SAMPLE_CSV).network)
    after = await store.read(tenant, "MATCH (n:Entity) RETURN count(n) AS n", op="t")
    assert before.first()["n"] == after.first()["n"]


async def test_single_points_totals_are_not_capped_by_limit(tools):
    full = await tools.single_points_of_failure(t.SinglePointsIn(limit=50))
    page = await tools.single_points_of_failure(t.SinglePointsIn(limit=2))
    assert len(page.data["parts"]) == 2
    assert page.data["total"] == full.data["total"] > 2
    assert (
        page.data["single_source_total"] + page.data["single_country_total"] == page.data["total"]
    )
    assert f"{full.data['total']} concentrated part(s)" in page.summary


async def test_reconnects_after_idle_sockets_are_dropped(store, tenant):
    # FalkorDB Cloud (or a NAT) closes idle sockets; the next query must reconnect.
    query = "MATCH (n:Entity) RETURN count(n) AS n"
    expected = (await store.read(tenant, query, op="t")).first()
    for conn in list(store._pool._available_connections):
        conn._writer.transport.abort()
    assert (await store.read(tenant, query, op="t")).first() == expected
