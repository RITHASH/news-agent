from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models import NewsArticle


@dataclass
class CacheEntry:
    """One cached news result for a single query.

    ``articles`` is the fully fetched, processed and (where applicable)
    summarized list. ``fetched_at`` is the monotonic time the data was
    completely ready. ``refreshing`` is set while a background refresh
    is in flight, so duplicate refreshes for the same key are prevented.
    """

    articles: List[NewsArticle]
    fetched_at: float
    query: str
    refreshing: bool = False


class NewsCache:
    """In-memory cache of fully processed news, keyed by query string.

    Design goals:

    * Within TTL: serve the cached, fully-processed articles with **zero**
      fetch / process / summarize work.
    * On staleness: keep serving the current entry while a **single**
      background refresh repopulates it; readers never block.
    * A single asyncio event loop means dict swaps are atomic — a reader
      can only ever observe the old entry or the new, complete one, never
      a partially built list (the replacement is fully constructed before
      it is stored). A per-key lock serializes the "is it stale? start a
      refresh?" decision so no two requests launch a duplicate refresh.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl = ttl_seconds
        self._store: Dict[str, CacheEntry] = {}
        # Populated lazily so constructing the cache needs no running loop.
        self._locks: Dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    def lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def get(self, query: str) -> Optional[CacheEntry]:
        return self._store.get(query)

    def is_fresh(self, entry: CacheEntry, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.monotonic()
        return (now - entry.fetched_at) < self.ttl

    def put(
        self, query: str, articles: List[NewsArticle], now: Optional[float] = None
    ) -> CacheEntry:
        if now is None:
            now = time.monotonic()
        # Atomic swap: readers see either the old entry or this complete one.
        entry = CacheEntry(articles=articles, fetched_at=now, query=query)
        self._store[query] = entry
        return entry

    def clear(self) -> None:
        self._store.clear()
