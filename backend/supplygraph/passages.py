"""Document passages: the retrieval half of the graph (GraphRAG).

A committed PDF is split into passages, each embedded and stored *in the same graph* as the
supply network, linked to the document and to the supply entities it mentions:

    (:Chunk {text, page, ordinal, embedding})-[:PART_OF]->(:Document)
    (:Entity)-[:MENTIONED_IN]->(:Chunk)

The naming follows FalkorDB's GraphRAG-SDK conventions. Because passages hang off the same
Supplier/Port/Region nodes the impact traversals use, a passage found by vector search leads
straight to the products and customers behind it.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from redis.exceptions import ResponseError

from .config import Settings
from .db import GraphStore

if TYPE_CHECKING:  # pragma: no cover
    from .llm import LLMProvider

log = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n{2,}")
_WS = re.compile(r"[ \t\r\f\v]+")
# Running headers/footers such as "Page 3" or "Page 3 of 10".
_PAGE_FOOTER = re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE)
WRITE_BATCH = 50


@dataclass(slots=True)
class Passage:
    ordinal: int
    page: int
    text: str
    mentions: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- text


def extract_pages(data: bytes) -> list[tuple[int, str]]:
    """Text per page (1-based). Scanned PDFs without a text layer yield nothing."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError) as exc:
        log.warning("pdf text extraction failed", extra={"error": str(exc)[:200]})
        return []
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf raises a variety of errors on damaged pages
            log.warning("page extraction failed", extra={"page": number, "error": str(exc)[:200]})
            continue
        text = "\n".join(
            _WS.sub(" ", line).strip() for line in text.splitlines() if not _PAGE_FOOTER.match(line)
        )
        if text.strip():
            pages.append((number, text.strip()))
    return pages


def chunk_pages(
    pages: list[tuple[int, str]], *, size: int, overlap: int, limit: int
) -> list[Passage]:
    """Pack sentences into passages of about ``size`` characters, carrying ``overlap``
    characters of trailing context into the next passage. Passages never span pages, so
    every passage can cite one page."""
    passages: list[Passage] = []
    for page_no, text in pages:
        units = [" ".join(u.split()) for u in _SENTENCE_SPLIT.split(text) if u.strip()]
        current: list[str] = []
        length = 0
        for unit in units:
            # hard-wrap pathological "sentences" (tables, long lists)
            pieces = [unit[i : i + size] for i in range(0, len(unit), size)] or [unit]
            for piece in pieces:
                if current and length + len(piece) + 1 > size:
                    passages.append(Passage(len(passages), page_no, " ".join(current)))
                    if len(passages) >= limit:
                        return passages
                    carry: list[str] = []
                    carried = 0
                    for prev in reversed(current):
                        if carried + len(prev) > overlap:
                            break
                        carry.insert(0, prev)
                        carried += len(prev) + 1
                    current, length = carry, carried
                current.append(piece)
                length += len(piece) + 1
        if current:
            passages.append(Passage(len(passages), page_no, " ".join(current)))
            if len(passages) >= limit:
                return passages
    return passages


def link_mentions(passages: list[Passage], surface_forms: dict[str, set[str]]) -> None:
    """Attach entity ids to passages that contain one of the entity's surface forms
    (its graph name or how the document wrote it). Case-insensitive, whole-word."""
    patterns = {
        entity_id: [
            re.compile(r"(?<![\w])" + re.escape(form.lower()) + r"(?![\w])")
            for form in forms
            if len(form.strip()) >= 3
        ]
        for entity_id, forms in surface_forms.items()
    }
    for p in passages:
        lowered = p.text.lower()
        for entity_id, regexes in patterns.items():
            if any(r.search(lowered) for r in regexes):
                p.mentions.add(entity_id)


# --------------------------------------------------------------------------- graph

Q_DELETE_CHUNKS = """
MATCH (c:Chunk)-[:PART_OF]->(:Document {sha256: $sha})
DETACH DELETE c
"""

Q_WRITE_CHUNKS = """
MATCH (doc:Document {sha256: $sha})
UNWIND $rows AS row
CREATE (c:Chunk {id: row.id, text: row.text, page: row.page, ordinal: row.ordinal,
                 embedding: vecf32(row.embedding)})-[:PART_OF]->(doc)
WITH c, row
UNWIND row.mentions AS eid
MATCH (e:Entity {id: eid})
MERGE (e)-[:MENTIONED_IN]->(c)
RETURN count(*) AS links
"""

Q_MARK_INDEXED = """
MATCH (doc:Document {sha256: $sha})
SET doc.passages = $n, doc.indexed_at = $ts, doc.embedding_model = $model
"""


async def ensure_vector_index(store: GraphStore, tenant: str, dimensions: int) -> None:
    graph = await store.raw_graph(tenant)
    for create in (
        lambda: graph.create_node_vector_index(
            "Chunk", "embedding", dim=dimensions, similarity_function="cosine"
        ),
        lambda: graph.create_node_range_index("Chunk", "id"),
    ):
        try:
            await create()
        except ResponseError as exc:
            if (
                "already indexed" not in str(exc).lower()
                and "already exists" not in str(exc).lower()
            ):
                raise


async def index_document(
    *,
    settings: Settings,
    store: GraphStore,
    provider: LLMProvider,
    tenant: str,
    sha256: str,
    document_id: str,
    data: bytes,
    surface_forms: dict[str, set[str]],
) -> dict[str, int]:
    """Split, embed and store a document's passages. Idempotent per document."""
    pages = extract_pages(data)
    passages = chunk_pages(
        pages,
        size=settings.passage_chars,
        overlap=settings.passage_overlap_chars,
        limit=settings.max_passages_per_document,
    )
    if not passages:
        return {"pages": len(pages), "passages": 0, "linked": 0}
    link_mentions(passages, surface_forms)
    vectors = await provider.embed([p.text for p in passages])

    await ensure_vector_index(store, tenant, settings.embedding_dimensions)
    await store.write(tenant, Q_DELETE_CHUNKS, {"sha": sha256}, op="passages:delete")
    rows = [
        {
            "id": f"{document_id}-P{p.ordinal:04d}",
            "text": p.text,
            "page": p.page,
            "ordinal": p.ordinal,
            "embedding": vec,
            "mentions": sorted(p.mentions),
        }
        for p, vec in zip(passages, vectors, strict=True)
    ]
    for i in range(0, len(rows), WRITE_BATCH):
        await store.write(
            tenant,
            Q_WRITE_CHUNKS,
            {"sha": sha256, "rows": rows[i : i + WRITE_BATCH]},
            op="passages:write",
        )
    await store.write(
        tenant,
        Q_MARK_INDEXED,
        {
            "sha": sha256,
            "n": len(rows),
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": settings.openai_embedding_model,
        },
        op="passages:mark",
    )
    linked = sum(1 for p in passages if p.mentions)
    log.info(
        "document indexed",
        extra={"tenant": tenant, "sha256": sha256, "passages": len(rows), "linked": linked},
    )
    return {"pages": len(pages), "passages": len(rows), "linked": linked}
