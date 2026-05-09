import asyncio
import inspect
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from loguru import logger
from mautrix.errors import MLimitExceeded, MatrixRequestError
from mautrix.types import EventType

from .messaging import answer


@dataclass(slots=True)
class EmojiButton:
    emoji: str
    data: Any = None


@dataclass
class EmojiKeyBoard:
    rows: Sequence[Union[EmojiButton, Sequence[EmojiButton]]]
    callback: Callable[["EmojiCallbackContext"], Awaitable[None]]

    timeout: int = 900
    allowed_senders: Union[str, Sequence[str], None] = None
    single_use: bool = False
    clear_on_timeout: bool = False
    remove_clicked: bool = True
    keep_reactions: bool = True
    preserve_order: bool = True
    newest_first: bool = True
    allow_sudo: bool = True
    data: dict = field(default_factory=dict)


EmojiCallback = Callable[["EmojiCallbackContext"], Awaitable[None] | None]
EmojiButtonsInput = Mapping[str, Any] | Sequence[EmojiButton | str | tuple]


def _normalize_emoji_buttons(
    buttons: EmojiButtonsInput,
) -> dict[str, EmojiButton]:
    if isinstance(buttons, Mapping):
        return {emoji: EmojiButton(emoji=emoji, data=data) for emoji, data in buttons.items()}
    return {btn.emoji: btn for btn in buttons}


async def attach_keyboard(mx, room_id: str, message_id: str, markup: EmojiKeyBoard, event: Any):
    flat_buttons = []
    for row in markup.rows:
        if isinstance(row, (list, tuple)):
            flat_buttons.extend(row)
        else:
            flat_buttons.append(row)

    allowed = markup.allowed_senders
    if allowed is None and event:
        allowed = {event.sender}

    await emoji_callback(
        mx=mx,
        room_id=room_id,
        message_id=message_id,
        buttons=flat_buttons,
        callback=markup.callback,
        allowed_senders=allowed,
        timeout=markup.timeout,
        single_use=markup.single_use,
        clear_reactions_on_timeout=markup.clear_on_timeout,
        remove_clicked=markup.remove_clicked,
        keep_reactions=markup.keep_reactions,
        preserve_order=markup.preserve_order,
        newest_first=markup.newest_first,
        allow_sudo=markup.allow_sudo,
        data=markup.data,
    )


Button = EmojiButton


def btn(key: str, payload: Any = None) -> EmojiButton:
    return EmojiButton(key=key, payload=key if payload is None else payload)


@dataclass(slots=True)
class EmojiPage:
    text: str
    page: int
    total: int
    html: bool = True


@dataclass(slots=True)
class EmojiCallbackContext:
    mx: Any
    session: "EmojiCallbackSession"
    event: Any
    key: str
    payload: Any
    message_id: str
    room_id: str
    sender: str | None
    reaction_event_id: str | None
    is_redaction: bool = False

    @property
    def data(self) -> dict[str, Any]:
        return self.session.data

    async def edit(self, text: str, html: bool = True, **kwargs) -> str | None:
        return await _api_with_retry(
            lambda: answer(
                self.mx,
                text=text,
                html=html,
                room_id=self.room_id,
                edit_id=self.message_id,
                **kwargs,
            ),
            _get_react_limiter(),
        )

    async def react(self, key: str) -> str | None:
        return await _react_with_retry(
            self.mx, self.room_id, self.message_id, key, _get_react_limiter(),
        )

    async def close(self, clear_reactions: bool = False) -> None:
        await self.session.close(clear_reactions=clear_reactions)

    async def refresh(self, key: str | None = None, force_order: bool = False) -> None:
        if force_order:
            await self.session.sync_reactions(force=True)
        else:
            await self.session.refresh(key)


_EMOJI_CALLBACKS_BY_MESSAGE: dict[str, "EmojiCallbackSession"] = {}
_EMOJI_CALLBACKS_BY_REACTION: dict[str, "EmojiCallbackSession"] = {}
_EMOJI_IGNORED_REDACTIONS: set[str] = set()

_REACT_LIMITER: "ReactionRateLimiter | None" = None


def _get_react_limiter() -> "ReactionRateLimiter":
    global _REACT_LIMITER
    if _REACT_LIMITER is None:
        _REACT_LIMITER = ReactionRateLimiter()
    return _REACT_LIMITER


class ReactionRateLimiter:
    def __init__(self, rate: float = 15.0, burst: int = 8):
        self.base_rate = rate
        self.rate = rate
        self.burst = burst
        self.min_rate = 2.0
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._consecutive_errors = 0

    async def acquire(self) -> float:
        async with self._lock:
            self._refill()
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return 0.0
            wait = (1.0 - self.tokens) / self.rate
            self.tokens = 0.0
            return wait

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def report_success(self) -> None:
        self._consecutive_errors = max(0, self._consecutive_errors - 1)
        if self._consecutive_errors == 0 and self.rate < self.base_rate:
            self.rate = min(self.base_rate, self.rate * 1.5)

    def report_error(self) -> None:
        self._consecutive_errors += 1
        self.rate = max(self.min_rate, self.rate * 0.5)
        self.tokens = 0.0
        self.last_refill = time.monotonic()


async def _api_with_retry(
    api_call: Callable[[], Awaitable[Any]],
    limiter: ReactionRateLimiter,
    max_retries: int = 3,
) -> Any | None:
    for attempt in range(max_retries):
        wait = await limiter.acquire()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            result = await api_call()
            limiter.report_success()
            return result
        except MLimitExceeded:
            limiter.report_error()
            await asyncio.sleep(min(1.0 * (2 ** attempt) + random.uniform(0, 0.5), 8.0))
            continue
        except MatrixRequestError as e:
            if e.http_status == 429:
                limiter.report_error()
                await asyncio.sleep(min(1.0 * (2 ** attempt) + random.uniform(0, 0.5), 8.0))
                continue
            raise
    return None


async def _react_with_retry(
    mx: Any,
    room_id: str,
    message_id: str,
    key: str,
    limiter: ReactionRateLimiter,
    max_retries: int = 3,
) -> str | None:
    return await _api_with_retry(
        lambda: mx.client.react(room_id, message_id, key),
        limiter, max_retries,
    )


async def _redact_with_retry(
    mx: Any,
    room_id: str,
    reaction_event_id: str,
    limiter: ReactionRateLimiter,
    max_retries: int = 3,
) -> None:
    await _api_with_retry(
        lambda: mx.client.redact(room_id, reaction_event_id),
        limiter, max_retries,
    )


def _normalize_allowed_senders(
    allowed_senders: str | Sequence[str] | set[str] | None,
) -> set[str] | None:
    if allowed_senders is None:
        return None
    if isinstance(allowed_senders, str):
        return {allowed_senders}
    return {str(sender) for sender in allowed_senders}


def _get_relation(event: Any) -> Any:
    content = getattr(event, "content", None)
    return getattr(content, "relates_to", None) or getattr(content, "_relates_to", None)


def _get_security(mx: Any) -> Any:
    return getattr(mx, "security", None) or getattr(getattr(mx, "_bot", None), "security", None)


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class EmojiCallbackSession:
    mx: Any
    room_id: str
    message_id: str
    buttons: dict[str, EmojiButton]
    callback: EmojiCallback
    allowed_senders: set[str] | None = None
    timeout: float | None = 900
    keep_reactions: bool = True
    remove_clicked: bool = True
    preserve_order: bool = True
    newest_first: bool = True
    allow_sudo: bool = True
    single_use: bool = False
    clear_reactions_on_timeout: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    reaction_events: dict[str, str] = field(default_factory=dict)
    closed: bool = False
    _expires_at: float | None = None
    _timeout_task: asyncio.Task | None = None

    async def start(self) -> "EmojiCallbackSession":
        _EMOJI_CALLBACKS_BY_MESSAGE[self.message_id] = self

        if self.timeout:
            self._expires_at = time.monotonic() + self.timeout
            self._timeout_task = asyncio.create_task(self._expire_later())

        if self.keep_reactions:
            await self.refresh()

        return self

    async def _expire_later(self) -> None:
        try:
            await asyncio.sleep(float(self.timeout or 1))
            if not self.closed and self.is_expired:
                await self.close(clear_reactions=self.clear_reactions_on_timeout)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug(f"Emoji callback timeout cleanup failed: {e}")

    @property
    def is_expired(self) -> bool:
        return self._expires_at is not None and time.monotonic() >= self._expires_at

    def _is_allowed(self, event: Any) -> bool:
        sender = getattr(event, "sender", None)
        if self.allowed_senders is None:
            return True
        if sender in self.allowed_senders:
            return True
        if not self.allow_sudo or not sender:
            return False

        security = _get_security(self.mx)
        if not security:
            return False

        return (
            sender in getattr(security, "owners", set())
            or sender in getattr(security, "sudos", set())
        )

    def _button_keys_for_send(self) -> list[str]:
        keys = list(self.buttons.keys())
        return list(keys) if self.newest_first else keys

    def _has_reaction_key(self, key: str) -> bool:
        return key in self.reaction_events.values()

    def _drop_reaction_event(self, reaction_event_id: str | None) -> None:
        if not reaction_event_id:
            return
        self.reaction_events.pop(reaction_event_id, None)
        _EMOJI_CALLBACKS_BY_REACTION.pop(reaction_event_id, None)

    async def refresh(self, key: str | None = None) -> None:
        if self.closed:
            return

        keys = [key] if key else self._button_keys_for_send()
        limiter = _get_react_limiter()

        for button_key in keys:
            if button_key not in self.buttons or self._has_reaction_key(button_key):
                continue
            reaction_id = await _react_with_retry(
                self.mx, self.room_id, self.message_id, button_key, limiter,
            )
            if reaction_id:
                self.reaction_events[reaction_id] = button_key
                _EMOJI_CALLBACKS_BY_REACTION[reaction_id] = self

    async def sync_reactions(self, force: bool = False) -> None:
        if self.closed:
            return

        if not force:
            await self.refresh()
            return

        limiter = _get_react_limiter()
        existing_keys = set(self.reaction_events.values())
        desired_keys = set(self.buttons.keys())

        extra = {rid: k for rid, k in self.reaction_events.items() if k not in desired_keys}
        for rid in extra:
            _EMOJI_CALLBACKS_BY_REACTION.pop(rid, None)
            _EMOJI_IGNORED_REDACTIONS.add(rid)
            try:
                await _redact_with_retry(self.mx, self.room_id, rid, limiter)
            except Exception:
                _EMOJI_IGNORED_REDACTIONS.discard(rid)
            self.reaction_events.pop(rid, None)

        missing = desired_keys - existing_keys
        for button_key in missing:
            if button_key not in self.buttons:
                continue
            reaction_id = await _react_with_retry(
                self.mx, self.room_id, self.message_id, button_key, limiter,
            )
            if reaction_id:
                self.reaction_events[reaction_id] = button_key
                _EMOJI_CALLBACKS_BY_REACTION[reaction_id] = self

    async def close(self, clear_reactions: bool = False) -> None:
        if self.closed:
            return

        self.closed = True
        _EMOJI_CALLBACKS_BY_MESSAGE.pop(self.message_id, None)

        reaction_ids = list(self.reaction_events)
        for reaction_id in reaction_ids:
            _EMOJI_CALLBACKS_BY_REACTION.pop(reaction_id, None)

        self.reaction_events.clear()

        if self._timeout_task and self._timeout_task is not asyncio.current_task():
            self._timeout_task.cancel()

        if clear_reactions:
            limiter = _get_react_limiter()
            for reaction_id in reaction_ids:
                try:
                    _EMOJI_IGNORED_REDACTIONS.add(reaction_id)
                    await _redact_with_retry(self.mx, self.room_id, reaction_id, limiter)
                except Exception:
                    _EMOJI_IGNORED_REDACTIONS.discard(reaction_id)

    async def handle(
        self,
        event: Any,
        key: str,
        reaction_event_id: str | None,
        is_redaction: bool,
    ) -> bool:
        if self.closed:
            return False

        if self.is_expired:
            await self.close(clear_reactions=self.clear_reactions_on_timeout)
            return True

        button = self.buttons.get(key)
        if not button:
            return False

        if is_redaction:
            self._drop_reaction_event(reaction_event_id)

        if not self._is_allowed(event):
            if is_redaction and self.keep_reactions:
                await self.sync_reactions(force=self.preserve_order)
            return True

        if not is_redaction and self.remove_clicked and reaction_event_id:
            try:
                await _redact_with_retry(
                    self.mx, self.room_id, reaction_event_id, _get_react_limiter(),
                )
            except Exception:
                pass

        ctx = EmojiCallbackContext(
            mx=self.mx,
            session=self,
            event=event,
            key=key,
            payload=button.data,
            message_id=self.message_id,
            room_id=self.room_id,
            sender=getattr(event, "sender", None),
            reaction_event_id=reaction_event_id,
            is_redaction=is_redaction,
        )

        token = self.mx._current_event.set(event)
        try:
            await _maybe_await(self.callback(ctx))
        except Exception as e:
            logger.exception(f"Emoji callback failed for {key!r}: {e}")
        finally:
            self.mx._current_event.reset(token)

        if self.closed:
            return True

        if self.single_use:
            await self.close(clear_reactions=False)
            return True

        if self.keep_reactions:
            await self.sync_reactions(force=is_redaction and self.preserve_order)

        return True


async def emoji_callback(
    mx: Any,
    room_id: str,
    message_id: str,
    buttons: EmojiButtonsInput,
    callback: EmojiCallback,
    *,
    allowed_senders: str | Sequence[str] | set[str] | None = None,
    timeout: float | None = 900,
    keep_reactions: bool = True,
    remove_clicked: bool = True,
    preserve_order: bool = True,
    newest_first: bool = True,
    allow_sudo: bool = True,
    single_use: bool = False,
    clear_reactions_on_timeout: bool = False,
    data: dict[str, Any] | None = None,
) -> EmojiCallbackSession:
    session = EmojiCallbackSession(
        mx=mx,
        room_id=room_id,
        message_id=message_id,
        buttons=_normalize_emoji_buttons(buttons),
        callback=callback,
        allowed_senders=_normalize_allowed_senders(allowed_senders),
        timeout=timeout,
        keep_reactions=keep_reactions,
        remove_clicked=remove_clicked,
        preserve_order=preserve_order,
        newest_first=newest_first,
        allow_sudo=allow_sudo,
        single_use=single_use,
        clear_reactions_on_timeout=clear_reactions_on_timeout,
        data=data or {},
    )
    return await session.start()


async def dispatch_emoji_callback(mx: Any, event: Any) -> bool:
    event_type = getattr(event, "type", None)

    if event_type == EventType.REACTION:
        relation = _get_relation(event)
        target_id = getattr(relation, "event_id", None)
        key = getattr(relation, "key", None)
        reaction_id = getattr(event, "event_id", None)

        if not target_id or not key:
            return False

        session = _EMOJI_CALLBACKS_BY_MESSAGE.get(target_id)
        if not session:
            return False

        if reaction_id in session.reaction_events:
            return True

        return await session.handle(
            event,
            key=str(key),
            reaction_event_id=reaction_id,
            is_redaction=False,
        )

    if event_type == EventType.ROOM_REDACTION:
        redacted_id = getattr(event, "redacts", None)
        if not redacted_id:
            return False

        if redacted_id in _EMOJI_IGNORED_REDACTIONS:
            _EMOJI_IGNORED_REDACTIONS.discard(redacted_id)
            return True

        message_session = _EMOJI_CALLBACKS_BY_MESSAGE.get(redacted_id)
        if message_session:
            await message_session.close(clear_reactions=False)
            return True

        session = _EMOJI_CALLBACKS_BY_REACTION.get(redacted_id)
        if not session:
            return False

        key = session.reaction_events.get(redacted_id)
        if not key:
            return False

        return await session.handle(
            event,
            key=key,
            reaction_event_id=redacted_id,
            is_redaction=True,
        )

    return False
