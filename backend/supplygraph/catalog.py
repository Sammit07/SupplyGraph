"""Per-tenant cache of entity names, used by the grounding check."""

from __future__ import annotations

import asyncio
import time

from .db import GraphStore
from .models import EntityRef
from .tools import SupplyGraphTools


class CatalogCache:
    def __init__(self, store: GraphStore, ttl_seconds: float = 60.0) -> None:
        self._store = store
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, list[EntityRef]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, tenant: str) -> list[EntityRef]:
        entry = self._entries.get(tenant)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        lock = self._locks.setdefault(tenant, asyncio.Lock())
        async with lock:
            entry = self._entries.get(tenant)
            if entry and time.monotonic() - entry[0] < self._ttl:
                return entry[1]
            names = await SupplyGraphTools(self._store, tenant).entity_names()
            self._entries[tenant] = (time.monotonic(), names)
            return names

    def invalidate(self, tenant: str | None = None) -> None:
        if tenant is None:
            self._entries.clear()
        else:
            self._entries.pop(tenant, None)
