"""CSV contract and parser tests (no database)."""

from __future__ import annotations

import csv
import io

import pytest

from supplygraph.datasets import DATASETS, DATASETS_BY_NAME, parse_bundle
from supplygraph.ingest import build_rows


def _edit(files: dict[str, bytes], name: str, fn) -> dict[str, bytes]:
    rows = list(csv.reader(io.StringIO(files[name].decode())))
    fn(rows)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return {**files, name: buf.getvalue().encode()}


def _errors(outcome) -> list[str]:
    return [i.message for i in outcome.issues if i.severity == "error"]


def test_sample_bundle_parses(sample_files):
    outcome = parse_bundle(sample_files)
    assert outcome.ok, _errors(outcome)
    net = outcome.network
    assert len(net.suppliers) == 42
    assert {p.id for p in net.products} == {"PRD-EVX", "PRD-PACK75", "PRD-HUB", "PRD-INFO"}
    assert outcome.files["sourcing.csv"] == "sourcing"
    # allocation_pct 60 -> share 0.6
    helios = next(s for s in net.suppliers if s.id == "SUP-001")
    assert helios.supplies[0].share == pytest.approx(0.6)


def test_files_are_matched_by_header_not_name(sample_files):
    renamed = {("EXPORT_" + k.upper()): v for k, v in sample_files.items()}
    outcome = parse_bundle(renamed)
    assert outcome.ok, _errors(outcome)
    assert outcome.files["EXPORT_ITEMS.CSV"] == "items"


def test_semicolon_delimited_excel_export(sample_files):
    text = sample_files["customers.csv"].decode().replace(",", ";")
    outcome = parse_bundle({**sample_files, "customers.csv": text.encode("cp1252")})
    assert outcome.ok, _errors(outcome)


def test_missing_required_dataset(sample_files):
    files = {k: v for k, v in sample_files.items() if k != "bom.csv"}
    outcome = parse_bundle(files)
    assert not outcome.ok
    assert any("bom" in m for m in _errors(outcome))


def test_cell_errors_carry_file_row_and_column(sample_files):
    files = _edit(sample_files, "sourcing.csv", lambda rows: rows[3].__setitem__(2, "soon"))
    outcome = parse_bundle(files)
    issue = next(i for i in outcome.issues if i.severity == "error")
    assert (issue.file, issue.row, issue.column) == ("sourcing.csv", 4, "lead_time_days")


def test_dangling_reference(sample_files):
    files = _edit(sample_files, "suppliers.csv", lambda rows: rows[1].__setitem__(2, "REG-NOWHERE"))
    assert any("REG-NOWHERE" in m for m in _errors(parse_bundle(files)))


def test_bom_cycle(sample_files):
    files = _edit(sample_files, "bom.csv", lambda rows: rows.append(["PRT-ALU", "PRT-ENCL", "1"]))
    assert any("cycle" in m for m in _errors(parse_bundle(files)))


def test_oversubscribed_allocation(sample_files):
    files = _edit(sample_files, "sourcing.csv", lambda rows: rows[2].__setitem__(3, "90"))
    assert any("PRT-CELL" in m for m in _errors(parse_bundle(files)))


def test_foreign_currency_rejected(sample_files):
    files = _edit(sample_files, "sales.csv", lambda rows: rows[1].__setitem__(3, "EUR"))
    assert any("EUR" in m for m in _errors(parse_bundle(files)))
    # ...but accepted when it is the workspace base currency
    eur = _edit(sample_files, "sales.csv", lambda rows: [r.__setitem__(3, "EUR") for r in rows[1:]])
    eur = _edit(eur, "sourcing.csv", lambda rows: [r.__setitem__(5, "EUR") for r in rows[1:]])
    assert parse_bundle(eur, base_currency="EUR").ok


def test_bad_enum(sample_files):
    files = _edit(sample_files, "items.csv", lambda rows: rows[1].__setitem__(2, "WIDGET"))
    assert any("must be one of" in m for m in _errors(parse_bundle(files)))


def test_templates_round_trip_headers():
    for d in DATASETS:
        header = next(csv.reader(io.StringIO(d.template_csv())))
        assert header == [c.name for c in d.columns]
    assert DATASETS_BY_NAME["sourcing"].required


def test_build_rows_counts(sample_files):
    net = parse_bundle(sample_files).network
    nodes, rels = build_rows(net)
    assert len(nodes["Supplier"]) == 42
    assert len(rels["SUPPLIES"]) == sum(len(s.supplies) for s in net.suppliers)
    evx = next(n for n in nodes["Product"] if n["id"] == "PRD-EVX")
    assert evx["props"]["annual_revenue"] == 2_400_000_000


def test_public_sample_is_valid_and_large():
    from conftest import PUBLIC_CSV, SAMPLE_PDF

    files = {p.name: p.read_bytes() for p in PUBLIC_CSV.glob("*.csv")}
    outcome = parse_bundle(files)
    assert outcome.ok, _errors(outcome)
    for dataset in (
        "locations",
        "suppliers",
        "items",
        "bom",
        "sourcing",
        "shipping_lanes",
        "customers",
        "sales",
    ):
        assert outcome.rows[dataset] >= 200, (dataset, outcome.rows[dataset])
    # the core scenario is embedded unchanged, so the sample PDFs still resolve
    names = {s.name for s in outcome.network.suppliers}
    assert {
        "Nakdong Display",
        "Penang Microcontrollers",
        "Suez Float Glass",
        "Ayutthaya Passive Components",
    } <= names
    import json

    manifest = json.loads((SAMPLE_PDF / "index.json").read_text(encoding="utf-8"))
    assert len(manifest) >= 6
    assert all((SAMPLE_PDF / d["file"]).exists() for d in manifest)
