# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import random
import time
from collections import deque
from typing import Any, Callable, Optional

from loguru import logger


class SpamBlock(Exception):
    pass


class GlobalRateLimiter:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._last_request: float = 0.0
        self.min_interval: float = 0.5

        self._ok_timeline: deque[float] = deque()
        self._429_timeline: deque[float] = deque()
        self._backoff_until: float = 0.0
        self._ok_since_last_429: int = 0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._trim(now)

            if now < self._backoff_until:
                await asyncio.sleep(self._backoff_until - now)
                now = time.monotonic()

            elapsed = now - self._last_request
            wait = self.min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

            self._last_request = time.monotonic()

    def report_success(self) -> None:
        now = time.monotonic()
        self._ok_timeline.append(now)

        if sum(1 for t in self._429_timeline if now - t < 60) == 0:
            self._ok_since_last_429 += 1
            if self._ok_since_last_429 >= 15:
                old = self.min_interval
                self.min_interval = max(0.4, self.min_interval * 0.95)
                self._ok_since_last_429 = 0
                logger.debug(f"Speedup {old:.3f} → {self.min_interval:.3f}s (15 OK, no 429)")
        else:
            self._ok_since_last_429 = 0

    def report_error(self) -> None:
        now = time.monotonic()
        self._429_timeline.append(now)
        self._ok_since_last_429 = 0

        recent = sum(1 for t in self._429_timeline if now - t < 15)
        self._backoff_until = now + max(self.min_interval * 2, min(recent * 3.0, 15.0))

        old = self.min_interval
        self.min_interval = min(15.0, self.min_interval * 1.50)
        logger.warning(f"Slowdown {old:.3f} → {self.min_interval:.3f}s [{self.state_str()}]")

    def _trim(self, now: float) -> None:
        cutoff = now - 60
        while self._ok_timeline and self._ok_timeline[0] < cutoff:
            self._ok_timeline.popleft()
        while self._429_timeline and self._429_timeline[0] < cutoff:
            self._429_timeline.popleft()

    def state_str(self) -> str:
        now = time.monotonic()
        oks = sum(1 for t in self._ok_timeline if now - t < 60)
        errs = sum(1 for t in self._429_timeline if now - t < 60)
        total = oks + errs
        pct = f"{errs/total*100:.1f}%" if total > 0 else "0%"
        return (
            f"interval={self.min_interval:.3f}s "
            f"429={pct} ({errs}/{total}) "
            f"backoff={max(0.0, self._backoff_until - now):.1f}s "
            f"ok_seq={self._ok_since_last_429}"
        )


_limiter = GlobalRateLimiter()
_original_send: Optional[Callable] = None
_patched: bool = False


def mautrix_rate_limit_patch(max_retries: int = 5) -> None:
    global _original_send, _patched
    if _patched:
        return

    from mautrix.api import HTTPAPI
    from mautrix.errors.request import MLimitExceeded, MatrixRequestError

    _original_send = HTTPAPI._send

    async def _rate_limited_send(
        self: Any,
        method: Any,
        url: Any,
        content: Any,
        query_params: Any,
        headers: Any,
    ) -> Any:
        is_get = str(method).upper() == "GET"

        if is_get:
            return await _original_send(
                self, method, url, content, query_params, headers
            )

        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            await _limiter.acquire()
            try:
                result = await _original_send(
                    self, method, url, content, query_params, headers
                )
                if attempt == 0:
                    _limiter.report_success()
                return result
            except MatrixRequestError as e:
                is_rate_limit = (
                    isinstance(e, MLimitExceeded) or e.http_status == 429
                )
                if not is_rate_limit:
                    raise
                if attempt == 0:
                    _limiter.report_error()
                last_error = e
                if attempt >= max_retries - 1:
                    break
                wait = min(2.0**attempt + random.uniform(0, 1.0), 30.0)
                logger.warning(
                    f"Rate limited (429) on {method} {url.path}, "
                    f"retry {attempt + 1}/{max_retries} in {wait:.1f}s "
                    f"[{_limiter.state_str()}]"
                )
                await asyncio.sleep(wait)

        raise last_error  # type: ignore[misc]

    HTTPAPI._send = _rate_limited_send
    _patched = True
    logger.info("Mautrix HTTPAPI patched with AIMD rate limiter")


def mautrix_rate_limit_unpatch() -> None:
    global _original_send, _patched
    if _patched and _original_send is not None:
        from mautrix.api import HTTPAPI

        HTTPAPI._send = _original_send
        _patched = False
        logger.info("Mautrix HTTPAPI AIMD rate limiter patch removed")
