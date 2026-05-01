"""Sliding-window rate limiter for the JPO API.

The official limits are: 10 requests/min for /api/patent/*, 5 requests/min
for /opdapi/*. Daily caps are tracked via ``result.remainAccessCount`` in
the response payload (handled by the client, not here).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Iterable


class RateLimiter:
    """Async sliding-window limiter. Awaits sleep when window is full."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = max_calls
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            if len(self._timestamps) >= self._max:
                wait = self._timestamps[0] + self._window - now
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._evict_expired(now)
            self._timestamps.append(now)

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    @property
    def in_flight_window(self) -> Iterable[float]:
        return tuple(self._timestamps)


# Defaults per JPO spec
def domestic_limiter() -> RateLimiter:
    return RateLimiter(max_calls=10, window_seconds=60)


def opd_limiter() -> RateLimiter:
    return RateLimiter(max_calls=5, window_seconds=60)
