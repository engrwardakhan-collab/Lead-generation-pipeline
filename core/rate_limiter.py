from __future__ import annotations

import asyncio
import random
import threading
import time


class AsyncRateLimiter:
    """Random-delay rate limiter for async (Playwright) code."""

    def __init__(self, min_delay: float, max_delay: float) -> None:
        self._min = min_delay
        self._max = max_delay
        self._last: float = 0.0

    async def wait(self) -> None:
        delay = random.uniform(self._min, self._max)
        elapsed = time.monotonic() - self._last
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last = time.monotonic()


class SyncRateLimiter:
    """Random-delay rate limiter for synchronous (requests) code. Thread-safe."""

    def __init__(self, min_delay: float, max_delay: float) -> None:
        self._min = min_delay
        self._max = max_delay
        self._last: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        delay = random.uniform(self._min, self._max)
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self._last = time.monotonic()
