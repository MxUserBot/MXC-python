# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import time
from typing import Any, Callable, Optional

from loguru import logger


class SpamBlock(Exception):
    pass


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float, name: str = ""):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.name = name
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    async def consume(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.refill_rate if self.refill_rate > 0 else 1.0
            await asyncio.sleep(wait)


class AdaptiveGate:
    def __init__(self, start_gap: float, max_gap: float = 10.0, min_gap: float = 0.0):
        self.max_gap = max_gap
        self.min_gap = min_gap
        self._gap = start_gap
        self._last: float = 0.0
        self._last_429 = time.monotonic()
        self._lock = asyncio.Lock()

    def current_gap(self) -> float:
        return self._gap

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()

            gap = self._gap
            if gap > self.min_gap and now - self._last_429 > 120:
                gap = max(self.min_gap, gap / 1.5)
                self._gap = gap

            remaining = gap - (now - self._last)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last = time.monotonic()

    def report_429(self) -> float:
        now = time.monotonic()
        if self._gap == 0:
            self._gap = 2.0
        else:
            self._gap = min(self.max_gap, self._gap * 2.0)
        self._last_429 = now
        return self._gap


_BUCKET = TokenBucket(5, 3, "global")
_REACTION = AdaptiveGate(start_gap=1.0, max_gap=10.0, min_gap=0.5)
_MESSAGE = AdaptiveGate(start_gap=0.0, max_gap=10.0, min_gap=0.0)
_STATE = AdaptiveGate(start_gap=0.0, max_gap=10.0, min_gap=0.0)
_original_send: Optional[Callable] = None
_patched: bool = False


def _pick_gate(url) -> Optional[AdaptiveGate]:
    path = str(url.path) if hasattr(url, "path") else str(url)
    if "/send/m.reaction" in path:
        return _REACTION
    if "/send/m.room.message" in path or "/send/m.room.encrypted" in path:
        return _MESSAGE
    if "/state/" in path or "/redact/" in path:
        return _STATE
    return None


def mautrix_rate_limit_patch() -> None:
    global _original_send, _patched
    if _patched:
        return

    from mautrix.api import HTTPAPI
    from mautrix.errors.request import MLimitExceeded, MatrixRequestError

    _original_send = HTTPAPI._send

    async def _rate_limited_send(
        self: Any, method: Any, url: Any, content: Any, query_params: Any, headers: Any
    ) -> Any:
        is_get = str(method).upper() == "GET"
        if is_get:
            return await _original_send(self, method, url, content, query_params, headers)

        gate = _pick_gate(url)

        for attempt in range(2):
            if gate:
                await gate.acquire()

            await _BUCKET.consume()

            try:
                return await _original_send(
                    self, method, url, content, query_params, headers
                )
            except MatrixRequestError as e:
                is_rate_limit = isinstance(e, MLimitExceeded) or e.http_status == 429
                if not is_rate_limit:
                    raise
                if attempt >= 1:
                    raise

                gap = gate.report_429() if gate else 0
                logger.warning(
                    f"429 on {method} {getattr(url, 'path', url)} "
                    f"retry via gap={gap:.1f}s"
                )

        raise RuntimeError("unreachable")

    HTTPAPI._send = _rate_limited_send
    _patched = True
    logger.info(
        "Mautrix HTTPAPI patched: "
        f"reaction=start/{_REACTION.current_gap():.1f}s "
        f"message=unlimited state=unlimited "
        f"bucket=({_BUCKET.capacity}/{_BUCKET.refill_rate}/s) 1 retry"
    )


def mautrix_rate_limit_unpatch() -> None:
    global _original_send, _patched
    if _patched and _original_send is not None:
        from mautrix.api import HTTPAPI

        HTTPAPI._send = _original_send
        _patched = False
        logger.info("Mautrix HTTPAPI rate limit patch removed")
