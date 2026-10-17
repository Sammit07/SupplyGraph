"""HTTP API (private Vercel service; the Next.js server is its only caller).

No ``from __future__ import annotations`` here: FastAPI must evaluate the dependency
aliases declared inside ``create_app`` (State, Tenant, ...) at definition time.
"""

import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, get_args

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .agent import AgentAnswer, SupplyGraphAgent, Turn
from .catalog import CatalogCache
from .config import TENANT_RE, Settings, get_settings
from .datasets import DATASETS, DATASETS_BY_NAME, parse_bundle
from .db import GraphQueryError, GraphStore, GraphUnavailableError, InvalidTenantError
from .documents import CommitRequest, DocumentError, DocumentService, Proposal
from .explore import ExploreService
from .ingest import ingest
from .llm import LLMUnavailableError, build_provider
from .logging_setup import configure_logging
from .models import ToolResult
from .registry import TOOLS, ToolCallError, call_tool
from .tools import EntityKind, SupplyGraphTools, toolbox_for

log = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    history: list[Turn] = Field(default_factory=list, max_length=20)


class DisruptionStatus(BaseModel):
    active: bool


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = GraphStore(settings)
        self.catalog = CatalogCache(self.store)
        # One LLM provider (and HTTP client) per process, reused across warm invocations.
        self.provider = build_provider(settings)
        self.agent = SupplyGraphAgent(settings, self.store, self.catalog, self.provider)

    def documents(self, tenant: str) -> DocumentService:
        return DocumentService(self.settings, self.store, tenant, self.provider)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if not settings.api_keys:
        log.warning("SUPPLYGRAPH_API_KEYS is empty: the API accepts unauthenticated calls")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.sg = AppState(settings)
        yield
        await app.state.sg.store.close()

    docs = (
        {"docs_url": "/docs", "openapi_url": "/openapi.json"}
        if settings.expose_docs
        else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )
    app = FastAPI(title="SupplyGraph API", version=__version__, lifespan=lifespan, **docs)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response

    # ------------------------------------------------------------------ errors

    def _err(status: int, code: str, detail: str) -> JSONResponse:
        return JSONResponse({"error": code, "detail": detail}, status_code=status)

    @app.exception_handler(GraphUnavailableError)
    async def _graph_down(_: Request, exc: GraphUnavailableError):
        return _err(503, "graph_unavailable", str(exc))

    @app.exception_handler(LLMUnavailableError)
    async def _llm_down(_: Request, exc: LLMUnavailableError):
        return _err(503, "llm_unavailable", str(exc))

    @app.exception_handler(InvalidTenantError)
    async def _bad_tenant(_: Request, exc: InvalidTenantError):
        return _err(400, "invalid_workspace", str(exc))

    @app.exception_handler(ToolCallError)
    async def _tool_error(_: Request, exc: ToolCallError):
        return _err(503 if exc.retryable else 422, "tool_error", str(exc))

    @app.exception_handler(DocumentError)
    async def _doc_error(_: Request, exc: DocumentError):
        return _err(422, "document_error", str(exc))

    @app.exception_handler(GraphQueryError)
    async def _query_error(_: Request, exc: GraphQueryError):
        # Already logged with the query name; don't echo database internals to the browser.
        return _err(500, "graph_query_failed", "A graph query failed. The error has been logged.")

    # ------------------------------------------------------------------ dependencies

    def state(request: Request) -> AppState:
        return request.app.state.sg

    def authorize(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not settings.api_keys:
            return
        supplied = (x_api_key or "").encode()
        if not any(
            hmac.compare_digest(supplied, k.get_secret_value().encode()) for k in settings.api_keys
        ):
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")

    def tenant(x_tenant: Annotated[str | None, Header()] = None) -> str:
        value = (x_tenant or settings.default_tenant).strip().lower()
        if not TENANT_RE.fullmatch(value):
            raise InvalidTenantError(
                "workspace names use lowercase letters, digits, '-' and '_' (max 63)"
            )
        return value

    State = Annotated[AppState, Depends(state)]
    Tenant = Annotated[str, Depends(tenant)]

    def explore(sg: State, tenant_id: Tenant) -> ExploreService:
        return ExploreService(settings, sg.store, tenant_id)

    def toolbox(
        sg: State, tenant_id: Tenant, x_session_id: Annotated[str | None, Header()] = None
    ) -> SupplyGraphTools:
        return toolbox_for(
            settings, sg.store, tenant_id, provider=sg.provider, session_id=x_session_id
        )

    Explore = Annotated[ExploreService, Depends(explore)]
    Toolbox = Annotated[SupplyGraphTools, Depends(toolbox)]

    # ------------------------------------------------------------------ health

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(sg: State):
        if await sg.store.ping():
            return {"status": "ready", "falkordb": "up"}
        return JSONResponse({"status": "not_ready", "falkordb": "down"}, status_code=503)

    v1 = APIRouter(dependencies=[Depends(authorize)])

    # ------------------------------------------------------------------ meta & workspaces

    @v1.get("/meta")
    async def meta(sg: State, tenant_id: Tenant) -> dict[str, Any]:
        return {
            "version": __version__,
            "workspace": tenant_id,
            "workspace_exists": await sg.store.exists(tenant_id),
            "currency": settings.base_currency,
            "llm_configured": sg.provider.configured(),
            "llm_provider": sg.provider.name,
            "model": sg.provider.model,
            "entity_kinds": list(get_args(EntityKind)),
            "limits": {
                "import_max_bytes": settings.import_max_bytes,
                "pdf_max_bytes": settings.pdf_max_bytes,
                "network_max_nodes": settings.network_max_nodes,
            },
            "datasets": [d.describe() for d in DATASETS],
            "tools": [
                {
                    "name": s.name,
                    "description": s.description,
                    "read_only": s.read_only,
                    "input_schema": s.input_schema(),
                }
                for s in TOOLS
            ],
        }

    @v1.get("/workspaces")
    async def workspaces(sg: State) -> dict[str, Any]:
        return {"workspaces": await sg.store.list_tenants(), "default": settings.default_tenant}

    @v1.delete("/workspaces/{name}")
    async def delete_workspace(
        name: str, sg: State, x_confirm: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        if not TENANT_RE.fullmatch(name):
            raise InvalidTenantError("invalid workspace name")
        if x_confirm != name:
            raise HTTPException(400, detail="send X-Confirm with the workspace name to delete it")
        await sg.store.drop(name)
        ExploreService.invalidate(name)
        sg.catalog.invalidate(name)
        return {"deleted": name}

    # ------------------------------------------------------------------ read models

    @v1.get("/overview")
    async def overview(svc: Explore) -> dict[str, Any]:
        return await svc.overview()

    @v1.get("/stats")
    async def stats(svc: Explore) -> dict[str, Any]:
        return await svc.stats()

    @v1.get("/network")
    async def network(svc: Explore, include_inactive: bool = False) -> dict[str, Any]:
        return await svc.network(include_inactive=include_inactive)

    @v1.get("/neighborhood")
    async def neighborhood(
        svc: Explore,
        id: Annotated[str, Query(min_length=1, max_length=128)],
        depth: Annotated[int, Query(ge=1, le=3)] = 2,
    ) -> dict[str, Any]:
        return await svc.neighborhood(id, depth)

    @v1.get("/entity")
    async def entity(svc: Explore, id: Annotated[str, Query(min_length=1, max_length=128)]):
        found = await svc.entity(id)
        if found is None:
            raise HTTPException(404, detail=f"no entity with id {id!r}")
        return found

    @v1.get("/search")
    async def search(
        svc: Explore,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kind: Annotated[list[EntityKind] | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        return await svc.search(q, list(kind) if kind else None)

    # ------------------------------------------------------------------ tools

    @v1.get("/tools")
    async def list_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "read_only": s.read_only,
                "input_schema": s.input_schema(),
            }
            for s in TOOLS
        ]

    @v1.post("/tools/{name}", response_model=ToolResult)
    async def run_tool(
        name: str,
        tools: Toolbox,
        tenant_id: Tenant,
        arguments: Annotated[dict[str, Any], Body(default_factory=dict)],
    ) -> ToolResult:
        result = await call_tool(tools, name, arguments)
        if name == "record_finding":
            ExploreService.invalidate(tenant_id)
        return result

    # ------------------------------------------------------------------ agent

    @v1.post("/agent/ask", response_model=AgentAnswer)
    async def ask(body: AskRequest, sg: State, tenant_id: Tenant) -> AgentAnswer:
        return await sg.agent.ask(
            body.question, tenant=tenant_id, session_id=body.session_id, history=body.history
        )

    @v1.post("/agent/stream")
    async def ask_stream(body: AskRequest, sg: State, tenant_id: Tenant) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            try:
                async for event in sg.agent.stream(
                    body.question,
                    tenant=tenant_id,
                    session_id=body.session_id,
                    history=body.history,
                ):
                    yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
            except (LLMUnavailableError, GraphUnavailableError) as exc:
                payload = {"type": "error", "detail": str(exc)}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
            except Exception:
                log.exception("agent stream failed")
                payload = {"type": "error", "detail": "internal error"}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ findings & disruptions

    @v1.get("/findings")
    async def findings(svc: Explore) -> list[dict[str, Any]]:
        return await svc.findings()

    @v1.delete("/findings/{finding_id}")
    async def delete_finding(finding_id: str, svc: Explore) -> dict[str, Any]:
        if not await svc.delete_finding(finding_id):
            raise HTTPException(404, detail="finding not found")
        return {"deleted": finding_id}

    @v1.patch("/disruptions/{disruption_id}")
    async def set_disruption(disruption_id: str, body: DisruptionStatus, svc: Explore):
        updated = await svc.set_disruption_active(disruption_id, body.active)
        if not updated:
            raise HTTPException(404, detail="disruption not found")
        return updated

    # ------------------------------------------------------------------ data import

    @v1.get("/datasets")
    async def datasets() -> list[dict[str, Any]]:
        return [d.describe() for d in DATASETS]

    @v1.get("/datasets/{name}/template", response_class=PlainTextResponse)
    async def dataset_template(name: str) -> PlainTextResponse:
        dataset = DATASETS_BY_NAME.get(name)
        if dataset is None:
            raise HTTPException(404, detail=f"unknown dataset {name!r}")
        return PlainTextResponse(
            dataset.template_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )

    @v1.post("/imports")
    async def import_csv(
        sg: State,
        tenant_id: Tenant,
        files: Annotated[list[UploadFile], File()],
        mode: Literal["merge", "replace"] = "merge",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, bytes] = {}
        total = 0
        for f in files:
            name = (f.filename or "upload.csv").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if not name.lower().endswith((".csv", ".txt", ".tsv")):
                raise HTTPException(422, detail=f"{name}: only CSV files are accepted")
            data = await f.read()
            total += len(data)
            if total > settings.import_max_bytes:
                raise HTTPException(413, detail="upload exceeds the import size limit")
            payload[name] = data
        outcome = parse_bundle(
            payload,
            base_currency=settings.base_currency,
            max_rows_per_file=settings.import_max_rows_per_file,
        )
        report: dict[str, Any] = {
            "ok": outcome.ok,
            "files": outcome.files,
            "rows": outcome.rows,
            "issues": [i.as_dict() for i in outcome.issues],
            "dry_run": dry_run,
            "mode": mode,
            "workspace": tenant_id,
        }
        if not outcome.ok or dry_run or outcome.network is None:
            return JSONResponse(report, status_code=200 if outcome.ok else 422)
        report["written"] = await ingest(sg.store, tenant_id, outcome.network, mode=mode)
        ExploreService.invalidate(tenant_id)
        sg.catalog.invalidate(tenant_id)
        return report

    # ------------------------------------------------------------------ documents (PDF)

    @v1.post("/documents/extract", response_model=Proposal)
    async def extract_document(
        sg: State, tenant_id: Tenant, file: Annotated[UploadFile, File()]
    ) -> Proposal:
        name = (file.filename or "document.pdf").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        data = await file.read(settings.pdf_max_bytes + 1)
        return await sg.documents(tenant_id).extract(data, name)

    @v1.post("/documents/commit")
    async def commit_document(
        sg: State,
        tenant_id: Tenant,
        payload: Annotated[str, Form(description="CommitRequest as JSON")],
        file: Annotated[UploadFile | None, File(description="The reviewed PDF")] = None,
    ) -> dict[str, Any]:
        """Commit reviewed events. Sending the PDF again also indexes its passages; its
        SHA-256 must match the reviewed proposal."""
        try:
            body = CommitRequest.model_validate_json(payload)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        data = await file.read(settings.pdf_max_bytes + 1) if file is not None else None
        result = await sg.documents(tenant_id).commit(body, data)
        ExploreService.invalidate(tenant_id)
        sg.catalog.invalidate(tenant_id)
        return result

    @v1.get("/documents")
    async def list_documents(sg: State, tenant_id: Tenant) -> list[dict[str, Any]]:
        return await sg.documents(tenant_id).list_documents()

    app.include_router(v1, prefix="/v1")
    return app
