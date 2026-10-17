"""Integration tests for the UI read models, PDF commit path, imports and the HTTP API."""

from __future__ import annotations

import pytest
from conftest import SAMPLE_CSV, TODAY

from supplygraph.documents import CommitRequest, DocumentError, DocumentMeta, DocumentService
from supplygraph.explore import ExploreService
from supplygraph.ingest import ingest, load_dataset_dir
from supplygraph.models import Outcome
from supplygraph.tools import DisruptionImpactIn, SupplyGraphTools

pytestmark = pytest.mark.integration


async def test_overview_aggregates_active_disruptions(settings, store, tenant):
    ExploreService.invalidate(tenant)
    ov = await ExploreService(settings, store, tenant).overview()
    assert ov["kpis"]["active_disruptions"] == 3
    # EVX (2.4B) + PACK75 (0.31B) + INFO (0.095B) stop under at least one active event
    assert ov["kpis"]["revenue_at_risk"] == 2_805_000_000
    assert ov["kpis"]["products_at_risk"] == 3
    assert ov["disruptions"][0]["revenue_at_risk"] >= ov["disruptions"][-1]["revenue_at_risk"]
    assert ov["suggestions"], "suggestions are derived from live data"
    assert ov["stats"]["nodes"]["Supplier"] == 42


async def test_network_marks_disrupted_suppliers(settings, store, tenant):
    net = await ExploreService(settings, store, tenant).network()
    status = {n["id"]: n["status"] for n in net["nodes"]}
    assert status["SUP-014"] == "disrupted"
    assert status["SUP-031"] == "degraded"
    assert status["DIS-2026-TYPHOON-KHH"] == "disruption"
    assert "DIS-2025-RTM-STRIKE" not in status  # inactive hidden by default
    assert any(x["type"] == "COMPONENT_OF" for x in net["links"])


async def test_neighborhood_entity_and_search(settings, store, tenant):
    svc = ExploreService(settings, store, tenant)
    hood = await svc.neighborhood("PRT-SOC", 1)
    ids = {n["id"] for n in hood["nodes"]}
    assert {"PRT-SOC", "SUP-014", "PRT-WAFER", "PRT-INFOMOD"} <= ids
    ent = await svc.entity("SUP-014")
    assert ent["kind"] == "Supplier" and ent["status"] == "disrupted"
    assert any(r["type"] == "SUPPLIES" and r["outgoing"] for r in ent["relationships"])
    assert await svc.entity("NOPE") is None
    hits = await svc.search("kaohs", ["Supplier"])
    assert [h["id"] for h in hits] == ["SUP-014"]


async def test_document_commit_creates_disruption_with_provenance(settings, store, tenant):
    docs = DocumentService(settings, store, tenant)
    meta = DocumentMeta(sha256="a" * 64, filename="busan.pdf", size_bytes=1000, issuer="BPLA")
    req = CommitRequest(
        document=meta,
        events=[
            {
                "id": "DIS-TEST-BUSAN",
                "title": "Port of Busan strike",
                "category": "labor",
                "severity": 3,
                "start_date": "2026-10-20",
                "expected_end_date": "2026-11-03",
                "affected_ids": ["PORT-BUSAN"],
            }
        ],
    )
    result = await docs.commit(req)
    assert result["disruptions"] == [{"id": "DIS-TEST-BUSAN", "affects": 1}]

    tools = SupplyGraphTools(store, tenant, today=lambda: TODAY)
    impact = await tools.disruption_impact(DisruptionImpactIn(entity="DIS-TEST-BUSAN"))
    hit = {s["id"] for s in impact.data["suppliers_hit"]}
    # Helios Cell, Gyeong Cathode and Nakdong Display export only through Busan
    assert hit == {"SUP-001", "SUP-008", "SUP-033"}
    assert {p["id"] for p in impact.data["products"] if p["status"] == "line_stop_risk"} == {
        "PRD-EVX",
        "PRD-INFO",
    }
    listed = await docs.list_documents()
    assert listed[0]["disruptions"][0]["id"] == "DIS-TEST-BUSAN"

    # resolving the event takes it out of every live analysis
    await ExploreService(settings, store, tenant).set_disruption_active("DIS-TEST-BUSAN", False)
    status = await tools.disrupted_supplier_ids()
    assert "SUP-033" not in status


async def test_document_commit_rejects_bad_targets(settings, store, tenant):
    docs = DocumentService(settings, store, tenant)
    meta = DocumentMeta(sha256="b" * 64, filename="x.pdf", size_bytes=10)
    for target, message in (("PRD-EVX", "regions, ports or suppliers"), ("NOPE", "unknown")):
        req = CommitRequest(
            document=meta,
            events=[
                {
                    "id": "DIS-X",
                    "title": "Bad",
                    "category": "other",
                    "severity": 1,
                    "start_date": "2026-10-01",
                    "affected_ids": [target],
                }
            ],
        )
        with pytest.raises(DocumentError, match=message):
            await docs.commit(req)


async def test_pdf_validation(settings, store, tenant):
    docs = DocumentService(settings, store, tenant)
    with pytest.raises(DocumentError, match="not a PDF"):
        await docs.extract(b"hello", "x.pdf")


async def test_replace_import_swaps_graph(store, unique_tenant):
    net = load_dataset_dir(SAMPLE_CSV).network
    await ingest(store, unique_tenant, net, mode="merge")
    await store.write(
        unique_tenant, "CREATE (:Entity:Region {id:'STALE', name:'Stale', kind:'Region'})", op="t"
    )
    await ingest(store, unique_tenant, net, mode="replace")
    rows = await store.read(
        unique_tenant, "MATCH (n:Entity {id:'STALE'}) RETURN count(n) AS n", op="t"
    )
    assert rows.first()["n"] == 0
    assert unique_tenant in await store.list_tenants()
    assert not await store.exists(unique_tenant + "--staging")
    await store.drop(unique_tenant)


def test_http_api_end_to_end(settings, tenant, sample_files):
    from fastapi.testclient import TestClient

    from supplygraph.api import create_app

    app = create_app(settings.model_copy(update={"api_keys": []}))
    with TestClient(app) as client:
        h = {"X-Tenant": tenant}
        assert client.get("/readyz").json()["falkordb"] == "up"
        meta = client.get("/v1/meta", headers=h).json()
        assert meta["workspace_exists"] and meta["currency"] == settings.base_currency
        assert {d["name"] for d in meta["datasets"]} >= {"items", "bom", "sourcing"}

        r = client.post("/v1/tools/alternate_suppliers", headers=h, json={"part": "PRT-MCU"})
        assert r.status_code == 200 and r.json()["outcome"] == "answered"
        bad = client.post(
            "/v1/tools/disruption_impact", headers=h, json={"entity": "x", "max_depth": 99}
        )
        assert bad.status_code == 422

        files = [("files", (name, data, "text/csv")) for name, data in sample_files.items()]
        dry = client.post("/v1/imports?dry_run=true", headers=h, files=files)
        assert dry.status_code == 200 and dry.json()["ok"] and "written" not in dry.json()

        tpl = client.get("/v1/datasets/sourcing/template", headers=h)
        assert tpl.text.startswith("supplier_id,item_id,lead_time_days")
        assert client.get("/v1/network", headers=h).json()["nodes"]
        assert client.get("/v1/entity", params={"id": "PRD-EVX"}, headers=h).status_code == 200
        assert client.get("/v1/workspaces", headers=h).json()["workspaces"]
        assert client.get("/v1/meta", headers={"X-Tenant": "Bad Name!"}).status_code == 400


class HashEmbedder:
    """Deterministic bag-of-words embeddings: shared words give high cosine similarity."""

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def configured(self) -> bool:
        return True

    async def embed(self, texts):
        import hashlib
        import math
        import re

        out = []
        for text in texts:
            v = [0.0] * self.dims
            for word in re.findall(r"[a-z]{3,}", text.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


async def test_document_passages_graphrag(settings, store, unique_tenant):
    from conftest import SAMPLE_PDF

    from supplygraph.tools import SearchDocumentsIn

    net = load_dataset_dir(SAMPLE_CSV).network
    await ingest(store, unique_tenant, net, mode="replace")
    cfg = settings.model_copy(update={"embedding_dimensions": 64, "passage_chars": 300})
    embedder = HashEmbedder(64)
    docs = DocumentService(cfg, store, unique_tenant, provider=embedder)  # type: ignore[arg-type]
    pdf = (SAMPLE_PDF / "busan-port-labour-advisory.pdf").read_bytes()
    import hashlib

    meta = DocumentMeta(
        sha256=hashlib.sha256(pdf).hexdigest(), filename="busan.pdf", size_bytes=len(pdf)
    )
    req = CommitRequest(
        document=meta,
        events=[
            {
                "id": "DIS-BUSAN-STRIKE",
                "title": "Port of Busan strike",
                "category": "labor",
                "severity": 3,
                "start_date": "2026-10-20",
                "affected_ids": ["PORT-BUSAN"],
                "mentions": {"PORT-BUSAN": "Port of Busan"},
            }
        ],
    )
    with pytest.raises(DocumentError, match="does not match"):
        await docs.commit(req, pdf + b" ")
    result = await docs.commit(req, pdf)
    assert result["passages"]["indexed"] and result["passages"]["passages"] >= 3
    assert result["passages"]["linked"] >= 1

    tools = SupplyGraphTools(
        store, unique_tenant, embedder=embedder.embed, min_passage_similarity=0.05
    )
    hit = await tools.search_documents(
        SearchDocumentsIn(query="workers strike container terminals")
    )
    assert hit.outcome == Outcome.ANSWERED
    top = hit.data["passages"][0]
    assert top["document"]["filename"] == "busan.pdf" and top["page"] == 1
    assert top["disruptions"][0]["id"] == "DIS-BUSAN-STRIKE"
    # graph hop: passages mentioning the port are tied to the PORT-BUSAN entity
    focused = await tools.search_documents(
        SearchDocumentsIn(query="vessel loading", entity="Port of Busan")
    )
    assert focused.outcome == Outcome.ANSWERED
    assert any(e.id == "PORT-BUSAN" for e in focused.entities)

    svc = ExploreService(cfg, store, unique_tenant)
    port = await svc.entity("PORT-BUSAN")
    assert port["passages"] and "Busan" in port["passages"][0]["text"]
    dis = await svc.entity("DIS-BUSAN-STRIKE")
    assert dis["passages"][0]["hits"] >= 1

    # re-committing the same document replaces its passages instead of duplicating them
    again = await docs.commit(req, pdf)
    rows = await store.read(unique_tenant, "MATCH (c:Chunk) RETURN count(c) AS n", op="t")
    assert rows.first()["n"] == again["passages"]["passages"]
    await store.drop(unique_tenant)


async def test_search_without_passages_is_not_in_graph(store, tenant):
    from supplygraph.tools import SearchDocumentsIn

    tools = SupplyGraphTools(store, tenant)
    r = await tools.search_documents(SearchDocumentsIn(query="anything"))
    assert r.outcome == Outcome.NOT_IN_GRAPH


async def test_document_matching_never_guesses_across_kinds(settings, store, tenant):
    from supplygraph.documents import ExtractedTarget

    docs = DocumentService(settings, store, tenant)
    # No region is called "Black Sea"; the only loose hit is a supplier. Must not auto-match.
    sea = await docs._resolve_target(
        ExtractedTarget(name="Black Sea", kind_hint="Region", evidence="shipping in the Black Sea")
    )
    assert sea.status == "ambiguous" and sea.match is None
    assert "SUP-018" in {c.id for c in sea.candidates}
    # Generic geographic words are ignored when comparing names.
    kat = await docs._resolve_target(
        ExtractedTarget(name="Katanga region", kind_hint="Region", evidence="exports from Katanga")
    )
    assert kat.status == "matched" and kat.match.id == "REG-CD-KAT"
    # Legal forms are ignored too, and a sole match of the hinted kind is accepted.
    nak = await docs._resolve_target(
        ExtractedTarget(name="Nakdong Display Co., Ltd.", kind_hint="Supplier", evidence="fire")
    )
    assert nak.status == "matched" and nak.match.id == "SUP-033"


async def test_new_workspace_reads_as_empty(settings, store, unique_tenant):
    # FalkorDB creates a graph on first write; a brand-new workspace must read as empty,
    # not fail with "Invalid graph operation on empty key".
    assert not await store.exists(unique_tenant)
    svc = ExploreService(settings, store, unique_tenant)
    ov = await svc.overview()
    assert ov["stats"]["nodes"] == {} and ov["kpis"]["active_disruptions"] == 0
    assert (await svc.network())["nodes"] == []
    assert await svc.search("anything", None) == []
    tools = SupplyGraphTools(store, unique_tenant)
    impact = await tools.disruption_impact(DisruptionImpactIn(entity="Port of Busan"))
    assert impact.outcome == Outcome.UNKNOWN_ENTITY
    assert not await store.exists(unique_tenant)  # reading never creates the graph


async def test_overview_counts_compound_losses(settings, store, unique_tenant):
    """Penang alone removes 20% of microcontroller supply and Kaohsiung (typhoon) 80%.
    Separately each only constrains; together the MCU has no supplier left, which stops
    products that no single disruption stops (the charging hub)."""
    from supplygraph.tools import CombinedImpactIn

    net = load_dataset_dir(SAMPLE_CSV).network
    await ingest(store, unique_tenant, net, mode="replace")
    svc = ExploreService(settings, store, unique_tenant)
    before = await svc.overview()
    assert before["kpis"]["revenue_at_risk"] == 2_805_000_000
    assert before["compound"]["products"] == []

    docs = DocumentService(settings, store, unique_tenant)
    meta = DocumentMeta(sha256="c" * 64, filename="penang.pdf", size_bytes=10)
    await docs.commit(
        CommitRequest(
            document=meta,
            events=[
                {
                    "id": "DIS-PENANG-CYBER",
                    "title": "Penang ransomware",
                    "category": "cyber",
                    "severity": 4,
                    "start_date": "2026-10-16",
                    "affected_ids": ["SUP-015"],
                }
            ],
        )
    )
    single = await SupplyGraphTools(store, unique_tenant).disruption_impact(
        DisruptionImpactIn(entity="DIS-PENANG-CYBER")
    )
    assert single.data["revenue_at_risk"] == 0  # on its own: constrained only
    assert single.data["compounded_parts"] == ["Automotive 32-bit Microcontroller"]
    assert "combined_impact" in single.summary

    combined = await SupplyGraphTools(store, unique_tenant).combined_impact(CombinedImpactIn())
    mcu = next(p for p in combined.data["parts"] if p["id"] == "PRT-MCU")
    assert mcu["share_lost"] == 1.0 and mcu["unaffected_alternates"] == []

    ExploreService.invalidate(unique_tenant)
    after = await svc.overview()
    assert [p["id"] for p in after["compound"]["products"]] == ["PRD-HUB"]
    assert after["kpis"]["revenue_at_risk"] == 2_805_000_000 + 180_000_000
    assert after["compound"]["blocked_parts"] == ["Automotive 32-bit Microcontroller"]
    await store.drop(unique_tenant)
