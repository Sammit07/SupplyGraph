"""FalkorDB access layer.

Every query in the product goes through :class:`GraphStore`. It enforces:

* tenant isolation by graph name (``<prefix>:<tenant>``), never by query filters;
* read-only execution (``GRAPH.RO_QUERY``) for everything except explicit writes;
* a server-side timeout on every query;
* structured timing logs, so each tool call can be traced to its Cypher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from falkordb.asyncio import FalkorDB
from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .config import TENANT_RE, Settings

log = logging.getLogger(__name__)

_CONNECT_BACKOFF_SECONDS = 10.0


class _StandaloneFalkorDB(FalkorDB):
    """``FalkorDB`` for standalone servers, built without the constructor's blocking probe.

    The async client only needs ``connection``, ``flushdb`` and ``execute_command``
    (checked against the pinned falkordb==1.8.0).
    """

    def __init__(self, connection: Redis) -> None:  # noqa: D107 - intentionally skips super()
        self.connection = connection
        self.flushdb = connection.flushdb
        self.execute_command = connection.execute_command


class GraphUnavailableError(RuntimeError):
    """FalkorDB could not be reached or timed out."""


class GraphQueryError(RuntimeError):
    """FalkorDB rejected a query (syntax, constraint, runtime error)."""


class InvalidTenantError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rows:
    """A query result as a list of dicts keyed by column name."""

    columns: list[str]
    rows: list[dict[str, Any]]
    elapsed_ms: float

    def __len__(self) -> int:
        return len(self.rows)

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


def graph_name(prefix: str, tenant: str) -> str:
    if not TENANT_RE.fullmatch(tenant):
        raise InvalidTenantError(f"invalid tenant id {tenant!r}")
    return f"{prefix}:{tenant}"


class GraphStore:
    """Async FalkorDB client bound to one deployment; graphs are selected per tenant."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        pool = BlockingConnectionPool(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            username=settings.falkordb_username,
            password=(
                settings.falkordb_password.get_secret_value()
                if settings.falkordb_password
                else None
            ),
            max_connections=settings.falkordb_max_connections,
            timeout=10,
            socket_connect_timeout=5,
            socket_timeout=(settings.query_timeout_ms / 1000) + 5,
            health_check_interval=30,
            socket_keepalive=True,
            # Idle pooled sockets get closed by the server or a NAT (and serverless functions
            # sleep between requests); retry those on a fresh connection. Timeouts are not
            # retried, so a slow write is never sent twice.
            retry=Retry(ExponentialBackoff(cap=1.0, base=0.1), 2),
            retry_on_error=[RedisConnectionError],
            decode_responses=True,
            **({"connection_class": _ssl_connection_class()} if settings.falkordb_ssl else {}),
        )
        self._pool = pool
        self._lock = asyncio.Lock()
        self._retry_after = 0.0
        # Standalone servers (FalkorDB Cloud, single node): no I/O at construction.
        self._db: FalkorDB | None = (
            None if settings.falkordb_cluster else _StandaloneFalkorDB(Redis(connection_pool=pool))
        )

    async def _client(self) -> FalkorDB:
        """Return the client, creating it on first use in cluster mode.

        ``falkordb.asyncio.FalkorDB`` probes the server with a *blocking* call in its
        constructor to detect cluster mode. In cluster mode we run that probe off the event
        loop, behind a short circuit breaker, so an unreachable server cannot stall requests.
        """
        if self._db is not None:
            return self._db
        async with self._lock:
            if self._db is not None:
                return self._db
            # Circuit breaker: after a failed connect, fail fast instead of re-probing on
            # every query (the probe retries with backoff and would stall each request).
            if time.monotonic() < self._retry_after:
                raise GraphUnavailableError("FalkorDB unavailable (retrying shortly)")
            try:
                self._db = await asyncio.wait_for(
                    asyncio.to_thread(FalkorDB, connection_pool=self._pool), timeout=8
                )
            except (RedisError, OSError, TimeoutError) as exc:
                self._retry_after = time.monotonic() + _CONNECT_BACKOFF_SECONDS
                log.error("falkordb connect failed", extra={"error": str(exc)[:300]})
                raise GraphUnavailableError(f"FalkorDB unavailable: {exc}") from exc
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.aclose()
        await self._pool.aclose()

    async def ping(self) -> bool:
        try:
            return bool(await (await self._client()).connection.ping())
        except (GraphUnavailableError, RedisError, OSError):
            return False

    async def list_graphs(self) -> list[str]:
        try:
            return await (await self._client()).list_graphs()
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise GraphUnavailableError(f"FalkorDB unavailable: {exc}") from exc

    async def list_tenants(self) -> list[str]:
        """Tenants that have a graph under this deployment's prefix (staging excluded)."""
        prefix = f"{self._settings.graph_prefix}:"
        return sorted(
            g[len(prefix) :]
            for g in await self.list_graphs()
            if g.startswith(prefix) and not g.endswith("--staging")
        )

    async def exists(self, tenant: str) -> bool:
        try:
            return bool(await (await self._client()).connection.exists(self.graph_id(tenant)))
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise GraphUnavailableError(f"FalkorDB unavailable: {exc}") from exc

    def graph_id(self, tenant: str) -> str:
        return graph_name(self._settings.graph_prefix, tenant)

    async def read(
        self, tenant: str, cypher: str, params: Mapping[str, Any] | None = None, *, op: str
    ) -> Rows:
        return await self._run(tenant, cypher, params, op=op, read_only=True)

    async def write(
        self, tenant: str, cypher: str, params: Mapping[str, Any] | None = None, *, op: str
    ) -> Rows:
        return await self._run(tenant, cypher, params, op=op, read_only=False)

    async def raw_graph(self, tenant: str):
        """Return the underlying AsyncGraph (used by schema management only)."""
        return (await self._client()).select_graph(self.graph_id(tenant))

    async def drop(self, tenant: str) -> None:
        if not await self.exists(tenant):
            return
        try:
            await (await self._client()).select_graph(self.graph_id(tenant)).delete()
        except ResponseError as exc:
            raise GraphQueryError(str(exc)) from exc

    async def replace_graph(self, *, source_tenant: str, target_tenant: str) -> None:
        """Make ``target`` an exact copy of ``source`` (GRAPH.COPY), replacing it."""
        source = (await self._client()).select_graph(self.graph_id(source_tenant))
        await self.drop(target_tenant)
        await source.copy(self.graph_id(target_tenant))

    async def _run(
        self,
        tenant: str,
        cypher: str,
        params: Mapping[str, Any] | None,
        *,
        op: str,
        read_only: bool,
    ) -> Rows:
        graph = (await self._client()).select_graph(self.graph_id(tenant))
        timeout = self._settings.query_timeout_ms
        start = time.perf_counter()
        try:
            if read_only:
                result = await graph.ro_query(cypher, dict(params or {}), timeout=timeout)
            else:
                result = await graph.query(cypher, dict(params or {}), timeout=timeout)
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            log.error("falkordb unavailable", extra={"op": op, "tenant": tenant})
            raise GraphUnavailableError(f"FalkorDB unavailable: {exc}") from exc
        except ResponseError as exc:
            msg = str(exc)
            if read_only and "empty key" in msg.lower():
                # FalkorDB creates a graph on its first write. Reading a workspace that has
                # no graph yet simply means there is no data.
                return Rows(columns=[], rows=[], elapsed_ms=(time.perf_counter() - start) * 1000)
            if "timed out" in msg.lower():
                raise GraphUnavailableError(f"query '{op}' exceeded {timeout} ms") from exc
            log.error("falkordb query error", extra={"op": op, "tenant": tenant, "error": msg})
            raise GraphQueryError(f"{op}: {msg}") from exc
        elapsed = (time.perf_counter() - start) * 1000
        columns = [_column_name(h) for h in (result.header or [])]
        rows = [dict(zip(columns, record, strict=True)) for record in (result.result_set or [])]
        log.debug(
            "query",
            extra={"op": op, "tenant": tenant, "rows": len(rows), "elapsed_ms": round(elapsed, 2)},
        )
        return Rows(columns=columns, rows=rows, elapsed_ms=elapsed)


def _column_name(header_entry: Any) -> str:
    # The client returns header entries as [type, name] pairs.
    if isinstance(header_entry, list | tuple) and len(header_entry) == 2:
        return str(header_entry[1])
    return str(header_entry)


def _ssl_connection_class():
    from redis.asyncio import SSLConnection

    return SSLConnection
