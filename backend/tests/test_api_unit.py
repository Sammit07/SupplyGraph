"""API wiring tests that need no database: dependency injection, validation, auth, errors."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from supplygraph.api import create_app
from supplygraph.config import Settings


@pytest.fixture
def client():
    # Point at a closed port so any accidental DB access fails fast and visibly.
    settings = Settings(_env_file=None, falkordb_port=1, log_json=False)
    with TestClient(create_app(settings)) as c:
        yield c


def test_health_and_dataset_contract(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    names = [d["name"] for d in client.get("/v1/datasets").json()]
    assert names[:2] == ["locations", "suppliers"]
    tpl = client.get("/v1/datasets/bom/template")
    assert tpl.headers["content-type"].startswith("text/csv")
    assert tpl.text.splitlines()[0] == "parent_item_id,component_item_id,quantity_per"
    assert client.get("/v1/datasets/nope/template").status_code == 404


def test_dependencies_resolve_from_headers(client):
    # Regression: string annotations once made FastAPI treat dependencies as query params.
    r = client.get("/v1/meta", headers={"X-Tenant": "Bad Name!"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_workspace"


def test_tool_validation_happens_before_graph_access(client):
    r = client.post("/v1/tools/disruption_impact", json={"entity": "x", "max_depth": 99})
    assert r.status_code == 422 and "max_depth" in r.json()["detail"]
    r = client.post("/v1/tools/not_a_tool", json={})
    assert r.status_code == 422 and "unknown tool" in r.json()["detail"]


def test_import_dry_run_needs_no_database(client, sample_files):
    files = [("files", (n, d, "text/csv")) for n, d in sample_files.items()]
    r = client.post("/v1/imports?dry_run=true", files=files)
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["rows"]["suppliers"] == 42


def test_import_reports_issues(client, sample_files):
    files = [("files", (n, d, "text/csv")) for n, d in sample_files.items() if n != "items.csv"]
    r = client.post("/v1/imports?dry_run=true", files=files)
    assert r.status_code == 422 and not r.json()["ok"]
    assert any("items" in i["message"] for i in r.json()["issues"])
    bad = client.post(
        "/v1/imports", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))]
    )
    assert bad.status_code == 422


def test_graph_outage_is_a_clean_503(client):
    r = client.get("/v1/overview")
    assert r.status_code == 503 and r.json()["error"] == "graph_unavailable"


def test_api_key_enforced():
    settings = Settings(_env_file=None, falkordb_port=1, api_keys=[SecretStr("k1")], log_json=False)
    with TestClient(create_app(settings)) as c:
        assert c.get("/v1/datasets").status_code == 401
        assert c.get("/v1/datasets", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/v1/datasets", headers={"X-API-Key": "k1"}).status_code == 200
        assert c.get("/healthz").status_code == 200


def test_api_keys_accept_comma_separated_env(monkeypatch):
    monkeypatch.setenv("SUPPLYGRAPH_API_KEYS", "a, b")
    assert [k.get_secret_value() for k in Settings(_env_file=None).api_keys] == ["a", "b"]
