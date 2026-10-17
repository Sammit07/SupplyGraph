from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest

from supplygraph.config import Settings
from supplygraph.db import GraphStore
from supplygraph.ingest import ingest, load_dataset_dir
from supplygraph.tools import SupplyGraphTools

TODAY = date(2026, 10, 16)
SAMPLES = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples"
# Tests assert hand-derived figures, so they run on a frozen core network. The larger public
# sample is only checked for validity and size.
SAMPLE_CSV = Path(__file__).resolve().parent / "fixtures" / "network"
PUBLIC_CSV = SAMPLES / "csv"
SAMPLE_PDF = SAMPLES / "pdf"


def _settings() -> Settings:
    # Reads SUPPLYGRAPH_* from the environment / backend/.env (e.g. FalkorDB Cloud).
    return Settings(api_keys=[], log_json=False)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return _settings()


@pytest.fixture(scope="session")
def sample_files() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(SAMPLE_CSV.glob("*.csv"))}


@pytest.fixture(scope="session")
async def store(settings: Settings):
    s = GraphStore(settings)
    if not await s.ping():
        await s.close()
        pytest.skip("FalkorDB is not reachable; set SUPPLYGRAPH_FALKORDB_* in backend/.env")
    yield s
    await s.close()


@pytest.fixture(scope="session")
async def tenant(store: GraphStore):
    name = f"test-{uuid.uuid4().hex[:10]}"
    outcome = load_dataset_dir(SAMPLE_CSV)
    assert outcome.ok, [i.as_dict() for i in outcome.issues]
    await ingest(store, name, outcome.network, mode="replace")
    yield name
    await store.drop(name)


@pytest.fixture
def tools(store: GraphStore, tenant: str) -> SupplyGraphTools:
    return SupplyGraphTools(store, tenant, session_id="pytest", today=lambda: TODAY)


@pytest.fixture
def unique_tenant() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


def pytest_report_header() -> list[str]:
    s = _settings()
    return [
        f"falkordb: {s.falkordb_host}:{s.falkordb_port} ssl={s.falkordb_ssl}",
        f"samples: {SAMPLES}",
        f"openai key set: {bool(os.getenv('OPENAI_API_KEY'))}",
    ]
