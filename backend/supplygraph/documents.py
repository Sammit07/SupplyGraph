"""PDF intake: turn supplier notices and risk bulletins into graph disruptions.

1. ``extract``: the LLM reads the PDF (native document input) and returns structured risk
   events. Every place or supplier it names is resolved against the graph's full-text
   index. Nothing is written; the result is a proposal for a human to review.
2. ``commit``: the reviewed proposal becomes ``Disruption`` nodes with ``AFFECTS`` edges to
   the confirmed entities, plus a ``Document`` node recording provenance:

       (:Document)-[:REPORTS]->(:Disruption)-[:AFFECTS]->(:Region|:Port|:Supplier)

From then on every tool sees the new disruption: impact analysis, exposure, alternates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .db import GraphStore
from .llm import LLMProvider, LLMUnavailableError, build_provider
from .models import EntityRef
from .passages import index_document
from .tools import SupplyGraphTools, names_overlap

log = logging.getLogger(__name__)

AFFECTABLE_KINDS = ["Region", "Port", "Supplier"]


class DocumentError(ValueError):
    """The upload is not an acceptable PDF or the proposal is invalid."""


# --------------------------------------------------------------------------- extraction schema


class ExtractedTarget(BaseModel):
    name: str = Field(description="Place, port or company exactly as written in the document.")
    kind_hint: Literal["Region", "Port", "Supplier", "Unknown"]
    evidence: str = Field(description="Short verbatim quote showing why it is affected.")


class ExtractedEvent(BaseModel):
    title: str
    category: Literal[
        "weather", "geopolitical", "labor", "operational", "logistics", "quality", "cyber", "other"
    ]
    severity: int = Field(description="1 minor ... 5 critical, judged from the document.")
    start_date: str | None = Field(description="ISO date YYYY-MM-DD, null if not stated.")
    expected_end_date: str | None = Field(description="ISO date YYYY-MM-DD, null if not stated.")
    summary: str = Field(description="Two sentences: what happened and the operational effect.")
    affected: list[ExtractedTarget]


class Extraction(BaseModel):
    is_supply_risk_document: bool
    issuer: str | None = Field(description="Organisation that issued the document, if stated.")
    events: list[ExtractedEvent]


EXTRACTION_PROMPT = """\
You read documents for a supply-chain risk team: supplier force-majeure notices, port or \
carrier advisories, commodity and regulatory bulletins, news reports.

Extract every distinct supply-chain risk event in the attached document. For each event, \
list each region, port or supplier company the document says is affected, using the names \
exactly as written, and quote the sentence that shows it. Do not add places or companies the \
document does not mention. Use only dates stated in the document; leave a date null when it \
is not given. If the document is not about a supply-chain risk, set is_supply_risk_document \
to false and return no events."""


# --------------------------------------------------------------------------- proposal / commit


class ProposedTarget(BaseModel):
    name: str
    kind_hint: str
    evidence: str
    match: EntityRef | None = None
    candidates: list[EntityRef] = Field(default_factory=list)
    status: Literal["matched", "ambiguous", "unmatched"]


class ProposedEvent(BaseModel):
    proposed_id: str
    title: str
    category: str
    severity: int
    start_date: str | None
    expected_end_date: str | None
    summary: str
    targets: list[ProposedTarget]


class DocumentMeta(BaseModel):
    sha256: str
    filename: str
    size_bytes: int
    issuer: str | None = None
    already_ingested: bool = False


class Proposal(BaseModel):
    document: DocumentMeta
    is_supply_risk_document: bool
    events: list[ProposedEvent]


class CommitEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    title: str = Field(min_length=3, max_length=300)
    category: str = Field(min_length=2, max_length=40)
    severity: int = Field(ge=1, le=5)
    start_date: date
    expected_end_date: date | None = None
    summary: str = Field(default="", max_length=2000)
    affected_ids: list[str] = Field(min_length=1, max_length=200)
    # entity id -> how the document wrote the name; used to link passages to entities
    mentions: dict[str, str] = Field(default_factory=dict, max_length=200)

    @field_validator("expected_end_date")
    @classmethod
    def _end_after_start(cls, v: date | None, info) -> date | None:
        start = info.data.get("start_date")
        if v and start and v < start:
            raise ValueError("expected_end_date is before start_date")
        return v


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: DocumentMeta
    events: list[CommitEvent] = Field(min_length=1, max_length=50)


Q_DOC_EXISTS = "MATCH (d:Document {sha256: $sha}) RETURN d.id AS id LIMIT 1"
Q_KINDS = "MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.kind AS kind, n.name AS name"
Q_COMMIT_DOC = """
MERGE (doc:Document {sha256: $sha})
ON CREATE SET doc.id = $doc_id, doc.filename = $filename, doc.size_bytes = $size,
              doc.issuer = $issuer, doc.uploaded_at = $ts
RETURN doc.id AS id
"""
Q_COMMIT_EVENT = """
MATCH (doc:Document {sha256: $sha})
MERGE (d:Entity:Disruption {id: $id})
SET d.kind = 'Disruption', d.name = $title, d.category = $category, d.severity = $severity,
    d.started_at = $start, d.expected_end = $end, d.active = true, d.summary = $summary,
    d.source = $filename
MERGE (doc)-[:REPORTS]->(d)
WITH d
UNWIND $targets AS tid
MATCH (t:Entity {id: tid})
MERGE (d)-[:AFFECTS]->(t)
RETURN d.id AS id, count(t) AS affects
"""
Q_DOCUMENTS = """
MATCH (doc:Document)
OPTIONAL MATCH (doc)-[:REPORTS]->(d:Disruption)
RETURN doc.id AS id, doc.filename AS filename, doc.issuer AS issuer,
       doc.uploaded_at AS uploaded_at, doc.sha256 AS sha256,
       coalesce(doc.passages, 0) AS passages,
       collect(CASE WHEN d IS NULL THEN NULL
                    ELSE {id: d.id, name: d.name, active: d.active} END) AS disruptions
ORDER BY doc.uploaded_at DESC
LIMIT 200
"""


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")[:40] or "EVENT"


class DocumentService:
    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        tenant: str,
        provider: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tenant = tenant
        self._provider = provider
        self.tools = SupplyGraphTools(store, tenant, currency=settings.base_currency)

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings)
        return self._provider

    def _validate_pdf(self, data: bytes) -> None:
        if len(data) > self.settings.pdf_max_bytes:
            raise DocumentError(
                f"PDF is {len(data)} bytes; the limit is {self.settings.pdf_max_bytes}"
            )
        if not data.startswith(b"%PDF-"):
            raise DocumentError("file is not a PDF")

    async def extract(self, data: bytes, filename: str) -> Proposal:
        self._validate_pdf(data)
        sha = hashlib.sha256(data).hexdigest()
        existing = await self.store.read(self.tenant, Q_DOC_EXISTS, {"sha": sha}, op="doc_exists")

        extraction = await self.provider.extract_pdf(
            data=data, filename=filename, prompt=EXTRACTION_PROMPT, schema=Extraction
        )

        events = []
        for i, ev in enumerate(extraction.events, start=1):
            targets = [await self._resolve_target(t) for t in ev.affected]
            start = _iso_or_none(ev.start_date)
            events.append(
                ProposedEvent(
                    proposed_id=f"DIS-{(start or datetime.now(UTC).date().isoformat())[:4]}-"
                    f"{_slug(ev.title)}-{sha[:6].upper()}{'' if i == 1 else f'-{i}'}",
                    title=ev.title,
                    category=ev.category,
                    severity=min(max(int(ev.severity), 1), 5),
                    start_date=start,
                    expected_end_date=_iso_or_none(ev.expected_end_date),
                    summary=ev.summary,
                    targets=targets,
                )
            )
        log.info(
            "document extracted",
            extra={"tenant": self.tenant, "sha256": sha, "events": len(events)},
        )
        return Proposal(
            document=DocumentMeta(
                sha256=sha,
                filename=filename,
                size_bytes=len(data),
                issuer=extraction.issuer,
                already_ingested=bool(existing.rows),
            ),
            is_supply_risk_document=extraction.is_supply_risk_document,
            events=events,
        )

    async def _resolve_target(self, target: ExtractedTarget) -> ProposedTarget:
        hinted = target.kind_hint in AFFECTABLE_KINDS
        kinds = [target.kind_hint] if hinted else AFFECTABLE_KINDS
        res = await self.tools.resolve(target.name, kinds)
        widened = False
        if res.entity is None and hinted:
            # the model's kind guess can be wrong; widen before giving up
            res = await self.tools.resolve(target.name, AFFECTABLE_KINDS)
            widened = True
        # Auto-match only when it is safe: an exact (normalised) name, or a sole partial
        # match of the kind the document implies whose name contains every word the document
        # used. Anything else needs a person to confirm, because a wrong match would disrupt
        # the wrong supplier.
        confident = res.entity is not None and (
            res.exact or (hinted and not widened and names_overlap(target.name, res.entity.name))
        )
        if confident:
            status = "matched"
        elif res.candidates:
            status = "ambiguous"
        else:
            status = "unmatched"
        return ProposedTarget(
            name=target.name,
            kind_hint=target.kind_hint,
            evidence=target.evidence,
            match=res.entity if confident else None,
            candidates=res.candidates[:8],
            status=status,
        )

    async def commit(self, request: CommitRequest, data: bytes | None = None) -> dict[str, Any]:
        """Write the reviewed events. With the original PDF bytes, also index its passages."""
        doc = request.document
        if data is not None:
            self._validate_pdf(data)
            if hashlib.sha256(data).hexdigest() != doc.sha256:
                raise DocumentError("the uploaded file does not match the reviewed document")

        ids = sorted({t for e in request.events for t in e.affected_ids})
        rows = await self.store.read(self.tenant, Q_KINDS, {"ids": ids}, op="commit:kinds")
        kinds = {r["id"]: r["kind"] for r in rows.rows}
        names = {r["id"]: r["name"] for r in rows.rows}
        missing = [i for i in ids if i not in kinds]
        wrong = [i for i in ids if i in kinds and kinds[i] not in AFFECTABLE_KINDS]
        if missing:
            raise DocumentError(f"unknown entity ids: {', '.join(missing)}")
        if wrong:
            raise DocumentError(
                f"disruptions can only affect regions, ports or suppliers: {', '.join(wrong)}"
            )

        ts = datetime.now(UTC).isoformat(timespec="seconds")
        await self.store.write(
            self.tenant,
            Q_COMMIT_DOC,
            {
                "sha": doc.sha256,
                "doc_id": f"DOC-{doc.sha256[:12].upper()}",
                "filename": doc.filename,
                "size": doc.size_bytes,
                "issuer": doc.issuer,
                "ts": ts,
            },
            op="commit:document",
        )
        created = []
        for ev in request.events:
            result = await self.store.write(
                self.tenant,
                Q_COMMIT_EVENT,
                {
                    "sha": doc.sha256,
                    "id": ev.id,
                    "title": ev.title,
                    "category": ev.category,
                    "severity": ev.severity,
                    "start": ev.start_date.isoformat(),
                    "end": ev.expected_end_date.isoformat() if ev.expected_end_date else None,
                    "summary": ev.summary,
                    "filename": doc.filename,
                    "targets": ev.affected_ids,
                },
                op="commit:event",
            )
            first = result.first() or {}
            created.append({"id": ev.id, "affects": first.get("affects", 0)})
        document_id = f"DOC-{doc.sha256[:12].upper()}"
        log.info("document committed", extra={"tenant": self.tenant, "events": created})

        passages: dict[str, Any] = {"passages": 0, "indexed": False}
        if data is not None:
            surface: dict[str, set[str]] = {i: {names[i]} for i in ids if names.get(i)}
            for ev in request.events:
                for entity_id, mention in ev.mentions.items():
                    if entity_id in surface and mention.strip():
                        surface[entity_id].add(mention.strip())
            try:
                stats = await index_document(
                    settings=self.settings,
                    store=self.store,
                    provider=self.provider,
                    tenant=self.tenant,
                    sha256=doc.sha256,
                    document_id=document_id,
                    data=data,
                    surface_forms=surface,
                )
                passages = {**stats, "indexed": True}
                if stats["passages"] == 0:
                    passages["warning"] = "no text layer found (scanned PDF?); passages not indexed"
            except LLMUnavailableError as exc:
                # The disruption is already committed; passages can be indexed later.
                passages["warning"] = f"passages not indexed: {exc}"
        return {"document_id": document_id, "disruptions": created, "passages": passages}

    async def list_documents(self) -> list[dict[str, Any]]:
        rows = await self.store.read(self.tenant, Q_DOCUMENTS, None, op="documents")
        return [{**r, "disruptions": [d for d in r["disruptions"] if d]} for r in rows.rows]


def _iso_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None
