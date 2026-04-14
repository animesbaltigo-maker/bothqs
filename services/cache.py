from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    stale_at: float
    touched_at: float


class TTLCache:
    def __init__(self, max_items: int = 512) -> None:
        self._items: dict[str, CacheEntry] = {}
        self._max_items = max(32, int(max_items))

    def get(self, key: str, *, allow_stale: bool = False) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None

        now = time.time()
        if now < item.expires_at:
            item.touched_at = now
            return item.value

        if allow_stale and now < item.stale_at:
            item.touched_at = now
            return item.value

        self._items.pop(key, None)
        return None

    def set(self, key: str, value: Any, ttl: int, *, stale_ttl: int | None = None) -> Any:
        ttl = max(1, int(ttl))
        stale_ttl = max(ttl, int(stale_ttl or ttl * 4))
        now = time.time()
        self._items[key] = CacheEntry(
            value=value,
            expires_at=now + ttl,
            stale_at=now + stale_ttl,
            touched_at=now,
        )
        self._prune(now)
        return value

    def pop(self, key: str) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def _prune(self, now: float | None = None) -> None:
        current_time = now or time.time()

        expired = [
            key
            for key, entry in self._items.items()
            if current_time >= entry.stale_at
        ]
        for key in expired:
            self._items.pop(key, None)

        overflow = len(self._items) - self._max_items
        if overflow <= 0:
            return

        oldest_keys = sorted(
            self._items.items(),
            key=lambda item: item[1].touched_at,
        )
        for key, _ in oldest_keys[:overflow]:
            self._items.pop(key, None)
