"""CSV dataset contract and parser.

Each dataset mirrors a common ERP / SCM extract (item master, bill of materials, approved
vendor list, ...). The specs below are the single source of truth: the parser validates
against them, the API serves them as downloadable templates, and the UI renders them.

Files are matched to datasets by their header row, not their filename, so exports named
``ITEM_MASTER_2026Q3.csv`` work as well as ``items.csv``.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import ValidationError

from .models import NetworkData

ColumnType = Literal["str", "int", "float", "date", "enum", "list"]


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: ColumnType
    description: str
    required: bool = True
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    example: str = ""


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    title: str
    description: str
    columns: tuple[Column, ...]
    required: bool = True

    @property
    def required_columns(self) -> set[str]:
        return {c.name for c in self.columns if c.required}

    def template_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow([c.name for c in self.columns])
        writer.writerow([c.example for c in self.columns])
        return buf.getvalue()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "required": self.required,
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "required": c.required,
                    "description": c.description,
                    "choices": list(c.choices),
                    "minimum": c.minimum,
                    "maximum": c.maximum,
                    "example": c.example,
                }
                for c in self.columns
            ],
        }


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "locations",
        "Locations",
        "Geographic master data: regions where suppliers operate and the ports they ship from.",
        (
            Column("location_id", "str", "Unique location code.", example="REG-TW-S"),
            Column(
                "location_type",
                "enum",
                "REGION or PORT.",
                choices=("REGION", "PORT"),
                example="REGION",
            ),
            Column("name", "str", "Display name.", example="Southern Taiwan"),
            Column("country_code", "str", "ISO 3166-1 alpha-2 country code.", example="TW"),
            Column(
                "parent_location_id",
                "str",
                "For ports: the region the port is in.",
                required=False,
                example="",
            ),
        ),
    ),
    Dataset(
        "suppliers",
        "Supplier master",
        "One row per supplier site.",
        (
            Column("supplier_id", "str", "Unique supplier code.", example="SUP-014"),
            Column(
                "supplier_name",
                "str",
                "Legal or trading name.",
                example="Kaohsiung Silicon Foundry",
            ),
            Column(
                "location_id",
                "str",
                "Region (location_type REGION) of the supplier site.",
                example="REG-TW-S",
            ),
            Column(
                "risk_score",
                "float",
                "Internal risk rating, 0 (low) to 1 (high).",
                required=False,
                minimum=0,
                maximum=1,
                example="0.45",
            ),
        ),
    ),
    Dataset(
        "shipping_lanes",
        "Shipping lanes",
        "Export ports each supplier ships through. A supplier is cut off only when every one "
        "of its ports is disrupted.",
        (
            Column("supplier_id", "str", "Supplier code.", example="SUP-014"),
            Column("port_id", "str", "Port location code.", example="PORT-KHH"),
        ),
        required=False,
    ),
    Dataset(
        "items",
        "Item master",
        "Every material number: finished goods, components and raw materials.",
        (
            Column("item_id", "str", "Unique item / material number.", example="PRT-SOC"),
            Column("item_name", "str", "Description.", example="Infotainment SoC (7nm)"),
            Column(
                "item_type",
                "enum",
                "FINISHED_GOOD, COMPONENT or RAW_MATERIAL.",
                choices=("FINISHED_GOOD", "COMPONENT", "RAW_MATERIAL"),
                example="COMPONENT",
            ),
            Column(
                "category",
                "str",
                "Commodity or category code.",
                required=False,
                example="semiconductor",
            ),
            Column(
                "days_on_hand",
                "int",
                "Inventory cover in days at this stage.",
                required=False,
                minimum=0,
                maximum=3650,
                example="30",
            ),
        ),
    ),
    Dataset(
        "bom",
        "Bill of materials",
        "Single-level BOM: one row per parent-component pair. Multi-level structure is "
        "reconstructed in the graph.",
        (
            Column("parent_item_id", "str", "Assembly or finished good.", example="PRT-INFOMOD"),
            Column(
                "component_item_id", "str", "Component consumed by the parent.", example="PRT-SOC"
            ),
            Column(
                "quantity_per",
                "float",
                "Units of component per unit of parent.",
                minimum=0,
                example="1",
            ),
        ),
    ),
    Dataset(
        "sourcing",
        "Approved vendor list",
        "Which supplier provides which item, at what allocation, lead time and price.",
        (
            Column("supplier_id", "str", "Supplier code.", example="SUP-014"),
            Column("item_id", "str", "Item supplied.", example="PRT-SOC"),
            Column(
                "lead_time_days",
                "int",
                "Order-to-delivery lead time.",
                minimum=0,
                maximum=730,
                example="120",
            ),
            Column(
                "allocation_pct",
                "float",
                "Share of volume allocated to this supplier.",
                minimum=0,
                maximum=100,
                example="100",
            ),
            Column(
                "unit_price", "float", "Price per unit.", required=False, minimum=0, example="48.00"
            ),
            Column(
                "currency", "str", "ISO 4217 currency of unit_price.", required=False, example="USD"
            ),
        ),
    ),
    Dataset(
        "customers",
        "Customer master",
        "Customers or sales channels.",
        (
            Column("customer_id", "str", "Unique customer code.", example="CUS-SERVICE"),
            Column("customer_name", "str", "Name.", example="Voltra Service Centers"),
            Column("segment", "str", "Segment or channel.", required=False, example="aftermarket"),
        ),
        required=False,
    ),
    Dataset(
        "sales",
        "Sales plan",
        "Annual revenue by finished good and customer; drives revenue-at-risk figures.",
        (
            Column("item_id", "str", "Finished good.", example="PRD-INFO"),
            Column("customer_id", "str", "Customer.", example="CUS-SERVICE"),
            Column("annual_revenue", "float", "Annual revenue.", minimum=0, example="95000000"),
            Column("currency", "str", "ISO 4217 currency.", required=False, example="USD"),
        ),
        required=False,
    ),
    Dataset(
        "risk_events",
        "Risk events",
        "Disruptions from a risk-monitoring feed. PDFs can also be ingested in the data hub.",
        (
            Column("event_id", "str", "Unique event id.", example="DIS-2026-TYPHOON-KHH"),
            Column("title", "str", "Short title.", example="Typhoon port closure (Kaohsiung)"),
            Column(
                "category",
                "str",
                "weather, geopolitical, labor, operational, ...",
                example="weather",
            ),
            Column(
                "severity", "int", "1 (minor) to 5 (critical).", minimum=1, maximum=5, example="4"
            ),
            Column("start_date", "date", "ISO date the event started.", example="2026-10-12"),
            Column(
                "expected_end_date",
                "date",
                "ISO date it is expected to end.",
                required=False,
                example="2026-11-05",
            ),
            Column(
                "status",
                "enum",
                "ACTIVE or RESOLVED.",
                choices=("ACTIVE", "RESOLVED"),
                example="ACTIVE",
            ),
            Column(
                "affected_ids",
                "list",
                "Semicolon-separated location or supplier ids the event affects.",
                example="PORT-KHH;REG-TW-S",
            ),
        ),
        required=False,
    ),
)

DATASETS_BY_NAME: dict[str, Dataset] = {d.name: d for d in DATASETS}


# --------------------------------------------------------------------------- parsing


@dataclass(slots=True)
class ImportIssue:
    severity: Literal["error", "warning"]
    message: str
    file: str | None = None
    row: int | None = None
    column: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("severity", "message", "file", "row", "column")}


@dataclass(slots=True)
class ParseOutcome:
    network: NetworkData | None
    issues: list[ImportIssue] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # filename -> dataset
    rows: dict[str, int] = field(default_factory=dict)  # dataset -> row count

    @property
    def ok(self) -> bool:
        return self.network is not None and not any(i.severity == "error" for i in self.issues)


class _Collector:
    def __init__(self, limit: int) -> None:
        self.issues: list[ImportIssue] = []
        self.limit = limit
        self.truncated = 0

    def add(self, issue: ImportIssue) -> None:
        if len(self.issues) < self.limit:
            self.issues.append(issue)
        else:
            self.truncated += 1

    def error(self, message: str, **where: Any) -> None:
        self.add(ImportIssue("error", message, **where))

    def warn(self, message: str, **where: Any) -> None:
        self.add(ImportIssue("warning", message, **where))

    @property
    def has_errors(self) -> bool:
        return self.truncated > 0 or any(i.severity == "error" for i in self.issues)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, 1, "not UTF-8 or Windows-1252 text")


def _normalise_header(name: str) -> str:
    return "_".join(name.strip().lower().replace("-", " ").split())


def _reader(text: str) -> csv.DictReader:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames:
        reader.fieldnames = [_normalise_header(h) for h in reader.fieldnames]
    return reader


def _match_dataset(header: list[str]) -> Dataset | None:
    cols = set(header)
    candidates = [d for d in DATASETS if d.required_columns <= cols]
    if not candidates:
        return None
    return max(candidates, key=lambda d: (len(cols & {c.name for c in d.columns}), len(d.columns)))


def _cell(col: Column, raw: str | None, sink: _Collector, where: dict[str, Any]) -> Any:
    value = (raw or "").strip()
    if not value:
        if col.required:
            sink.error("value is required", column=col.name, **where)
        return None
    try:
        if col.type == "int":
            parsed: Any = int(float(value.replace(",", "")))
        elif col.type == "float":
            parsed = float(value.replace(",", ""))
        elif col.type == "date":
            parsed = date.fromisoformat(value[:10])
        elif col.type == "enum":
            parsed = value.upper()
            if parsed not in col.choices:
                sink.error(f"must be one of {', '.join(col.choices)}", column=col.name, **where)
                return None
        elif col.type == "list":
            parsed = [v.strip() for v in value.replace(",", ";").split(";") if v.strip()]
        else:
            parsed = value
    except ValueError:
        sink.error(f"cannot parse {value!r} as {col.type}", column=col.name, **where)
        return None
    if isinstance(parsed, int | float):
        if col.minimum is not None and parsed < col.minimum:
            sink.error(f"must be >= {col.minimum:g}", column=col.name, **where)
            return None
        if col.maximum is not None and parsed > col.maximum:
            sink.error(f"must be <= {col.maximum:g}", column=col.name, **where)
            return None
    return parsed


def parse_bundle(
    files: dict[str, bytes],
    *,
    base_currency: str = "USD",
    max_rows_per_file: int = 200_000,
    max_issues: int = 200,
) -> ParseOutcome:
    """Parse uploaded CSV files into a validated :class:`NetworkData`."""
    sink = _Collector(max_issues)
    tables: dict[str, list[dict[str, Any]]] = {}
    matched: dict[str, str] = {}

    for filename, raw in sorted(files.items()):
        try:
            text = _decode(raw)
        except UnicodeDecodeError as exc:
            sink.error(str(exc), file=filename)
            continue
        reader = _reader(text)
        header = reader.fieldnames or []
        dataset = _match_dataset(header)
        if dataset is None:
            sink.error(
                "header does not match any dataset; download a template from the data hub",
                file=filename,
            )
            continue
        if dataset.name in tables:
            sink.error(f"second file for dataset '{dataset.name}'", file=filename)
            continue
        matched[filename] = dataset.name
        unknown = [h for h in header if h not in {c.name for c in dataset.columns}]
        if unknown:
            sink.warn(f"ignoring unknown columns: {', '.join(unknown)}", file=filename)
        rows: list[dict[str, Any]] = []
        for line_no, record in enumerate(reader, start=2):
            if line_no - 1 > max_rows_per_file:
                sink.error(f"more than {max_rows_per_file} rows", file=filename)
                break
            if not any((v or "").strip() for v in record.values() if isinstance(v, str)):
                continue  # blank line
            where = {"file": filename, "row": line_no}
            rows.append(
                {c.name: _cell(c, record.get(c.name), sink, where) for c in dataset.columns}
                | {"_row": line_no, "_file": filename}
            )
        tables[dataset.name] = rows

    for d in DATASETS:
        if d.required and d.name not in tables:
            sink.error(f"missing required dataset '{d.name}' ({d.title})")

    outcome = ParseOutcome(
        network=None,
        files=matched,
        rows={k: len(v) for k, v in tables.items()},
    )
    if sink.has_errors:
        outcome.issues = _finish(sink)
        return outcome

    document = _assemble(tables, sink, base_currency)
    if not sink.has_errors:
        try:
            outcome.network = NetworkData.model_validate(document)
        except ValidationError as exc:
            for err in exc.errors():
                for line in str(err["msg"]).splitlines():
                    line = line.strip(" -")
                    if line and "failed integrity checks" not in line:
                        sink.error(line.removeprefix("Value error, "))
    outcome.issues = _finish(sink)
    return outcome


def _finish(sink: _Collector) -> list[ImportIssue]:
    issues = list(sink.issues)
    if sink.truncated:
        issues.append(ImportIssue("error", f"{sink.truncated} more issues not shown"))
    return issues


def _assemble(
    tables: dict[str, list[dict[str, Any]]], sink: _Collector, base_currency: str
) -> dict[str, Any]:
    """Map the flat tables onto the nested NetworkData document."""

    def where(row: dict[str, Any]) -> dict[str, Any]:
        return {"file": row["_file"], "row": row["_row"]}

    def check_currency(row: dict[str, Any], column: str = "currency") -> None:
        cur = (row.get(column) or base_currency).upper()
        if cur != base_currency:
            sink.error(
                f"currency {cur} differs from the workspace base currency {base_currency}; "
                "convert before import",
                column=column,
                **where(row),
            )

    locations = tables.get("locations", [])
    location_type = {r["location_id"]: r["location_type"] for r in locations}
    regions = [
        {"id": r["location_id"], "name": r["name"], "country": (r["country_code"] or "").upper()}
        for r in locations
        if r["location_type"] == "REGION"
    ]
    ports = []
    for r in locations:
        if r["location_type"] != "PORT":
            continue
        parent = r["parent_location_id"]
        if not parent or location_type.get(parent) != "REGION":
            sink.error(
                f"port parent_location_id {parent!r} is not a known REGION",
                column="parent_location_id",
                **where(r),
            )
            continue
        ports.append({"id": r["location_id"], "name": r["name"], "region": parent})

    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in tables.get("bom", []):
        components[r["parent_item_id"]].append(
            {"part": r["component_item_id"], "qty": r["quantity_per"]}
        )

    sales: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in tables.get("sales", []):
        check_currency(r)
        sales[r["item_id"]][r["customer_id"]] += r["annual_revenue"]

    item_type: dict[str, str] = {}
    parts, products = [], []
    for r in tables.get("items", []):
        item_type[r["item_id"]] = r["item_type"]
        if r["item_type"] == "FINISHED_GOOD":
            products.append(
                {
                    "id": r["item_id"],
                    "name": r["item_name"],
                    "components": components.get(r["item_id"], []),
                    "customers": [
                        {"customer": c, "annual_revenue": v}
                        for c, v in sales.get(r["item_id"], {}).items()
                    ],
                }
            )
            if not components.get(r["item_id"]):
                sink.warn(f"finished good {r['item_id']} has no BOM rows", **where(r))
        else:
            parts.append(
                {
                    "id": r["item_id"],
                    "name": r["item_name"],
                    "category": r["category"] or r["item_type"].lower(),
                    "item_type": r["item_type"],
                    "inventory_days": r["days_on_hand"] or 0,
                    "components": components.get(r["item_id"], []),
                }
            )
    for r in tables.get("bom", []):
        if item_type.get(r["component_item_id"]) == "FINISHED_GOOD":
            sink.error(
                "a finished good cannot be a BOM component; model it as a COMPONENT",
                column="component_item_id",
                **where(r),
            )

    lanes: dict[str, list[str]] = defaultdict(list)
    for r in tables.get("shipping_lanes", []):
        lanes[r["supplier_id"]].append(r["port_id"])
    supplies: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in tables.get("sourcing", []):
        check_currency(r)
        supplies[r["supplier_id"]].append(
            {
                "part": r["item_id"],
                "lead_time_days": r["lead_time_days"],
                "share": round(r["allocation_pct"] / 100.0, 6),
                "unit_cost": r["unit_price"] or 0.0,
            }
        )
    suppliers = []
    for r in tables.get("suppliers", []):
        if location_type.get(r["location_id"]) != "REGION":
            sink.error(
                f"location_id {r['location_id']!r} is not a known REGION",
                column="location_id",
                **where(r),
            )
        if not supplies.get(r["supplier_id"]):
            sink.warn(f"supplier {r['supplier_id']} has no sourcing rows", **where(r))
        suppliers.append(
            {
                "id": r["supplier_id"],
                "name": r["supplier_name"],
                "region": r["location_id"],
                "ports": sorted(set(lanes.get(r["supplier_id"], []))),
                "risk_score": r["risk_score"] if r["risk_score"] is not None else 0.0,
                "supplies": supplies.get(r["supplier_id"], []),
            }
        )

    customers = [
        {"id": r["customer_id"], "name": r["customer_name"], "segment": r["segment"] or ""}
        for r in tables.get("customers", [])
    ]
    disruptions = [
        {
            "id": r["event_id"],
            "name": r["title"],
            "category": r["category"],
            "severity": r["severity"],
            "started_at": r["start_date"],
            "expected_end": r["expected_end_date"],
            "active": r["status"] == "ACTIVE",
            "affects": r["affected_ids"] or [],
        }
        for r in tables.get("risk_events", [])
    ]
    return {
        "regions": regions,
        "ports": ports,
        "parts": parts,
        "customers": customers,
        "products": products,
        "suppliers": suppliers,
        "disruptions": disruptions,
    }
