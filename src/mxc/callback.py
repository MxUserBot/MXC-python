# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import inspect
import time
from functools import lru_cache
from typing import Any

from loguru import logger
from mautrix.types import EventType, MessageEvent
from pydantic import ConfigDict, ValidationError, validate_call

pd_config = ConfigDict(arbitrary_types_allowed=True)


class BaseCallBack:
    def __init__(self, bot):
        self.bot = bot
        self._dispatched: dict[str, float] = {}

    async def get_perm_module(self, mod):
        return self.bot.interface

    async def _wrap_event(self, evt: MessageEvent) -> Any:
        from mxc.utils import answer, get_reply_text

        async def reply(text: str, html: bool = True):
            event_id = await answer(self.bot.interface, text, html=html, event=evt)
            self.bot._ignore_ids.add(event_id)
            return event_id

        async def react(key: str):
            return await self.bot.client.react(evt.room_id, evt.event_id, key)

        async def get_reply_text_wrapper():
            return await get_reply_text(self.bot.interface, evt)

        if hasattr(evt, "room_id") and hasattr(evt, "event_id"):
            evt.reply = reply
            evt.react = react
            evt.get_reply_text = get_reply_text_wrapper

        return evt

    async def _dispatch_event(self, evt: Any) -> None:
        if hasattr(evt, "event_id"):
            now = time.time()
            if evt.event_id in self._dispatched and now - self._dispatched[evt.event_id] < 5:
                return
            self._dispatched[evt.event_id] = now
            if len(self._dispatched) > 1000:
                cutoff = now - 60
                self._dispatched = {k: v for k, v in self._dispatched.items() if v > cutoff}
        from mxc.utils import dispatch_emoji_callback

        event_type = evt.type
        if event_type in (EventType.REACTION, EventType.ROOM_REDACTION):
            if await dispatch_emoji_callback(self.bot.interface, evt):
                return

        wrapped_evt = evt
        if isinstance(evt, MessageEvent):
            wrapped_evt = await self._wrap_event(evt)

        for mod in self.bot.active_modules.values():
            if not mod.enabled or not getattr(mod, "_is_ready", False):
                continue

            handlers = getattr(mod, "_event_handlers", {}).get(event_type, [])
            for handler in handlers:
                asyncio.create_task(self._safe_run(mod, handler, wrapped_evt))

    def _get_handler_params(self, func: callable, reserved_count: int) -> list[inspect.Parameter]:
        orig_f = getattr(func, "__func__", func)
        sig = inspect.signature(orig_f)
        return list(sig.parameters.values())[reserved_count:]

    def _extract_validation_message(self, error: ValidationError) -> str:
        return str(error.errors(include_url=False)[0].get("msg", "Validation error")) if error.errors() else "Validation error"

    def _build_handler_kwargs(
        self,
        params: list[inspect.Parameter],
        raw_input: Any = None,
    ) -> dict[str, Any]:
        if not params:
            return {}

        kwargs: dict[str, Any] = {}
        source = raw_input

        if len(params) == 1:
            if source in (None, ""):
                if params[0].default is inspect.Parameter.empty:
                    kwargs[params[0].name] = ""
                return kwargs
            kwargs[params[0].name] = source
            return kwargs

        if isinstance(source, str):
            words = source.split(maxsplit=len(params) - 1) if source else []
        elif source is None:
            words = []
        else:
            words = [source]

        for i, word in enumerate(words):
            if i < len(params):
                kwargs[params[i].name] = word

        return kwargs

    @lru_cache(maxsize=None)
    def _make_validated(self, func: callable) -> callable:
        return validate_call(func, config=pd_config)

    async def _invoke_validated(
        self,
        func: callable,
        reserved_args: list[Any],
        reserved_count: int,
        raw_input: Any = None,
    ) -> None:
        params = self._get_handler_params(func, reserved_count)
        kwargs = self._build_handler_kwargs(
            params=params,
            raw_input=raw_input,
        )

        v_func = self._make_validated(func)
        await v_func(*reserved_args, **kwargs)

    async def _raw_input_from_event(self, wrapped_evt: Any) -> Any:
        content = getattr(wrapped_evt, "content", None)
        if content:
            relates = getattr(content, "relates_to", None) or getattr(content, "_relates_to", None)
            if relates and getattr(relates, "rel_type", None) == "m.replace":
                new_content = getattr(content, "new_content", None)
                if new_content:
                    raw_input = getattr(new_content, "body", None)
                    if raw_input:
                        return raw_input
        raw_input = getattr(content, "body", None)
        if raw_input is None:
            raw_input = content
        return raw_input

    async def _safe_run(
        self,
        mod: Any,
        func: callable,
        wrapped_evt: Any,
        reserved_count: int = 3,
        extra_args: list = None,
        match: Any = None,
        reply_on_validation_error: bool = False,
    ) -> None:
        try:
            raw_input = await self._raw_input_from_event(wrapped_evt)
            if match:
                groups = match.groups()
                raw_input = (
                    match.groupdict()
                    or (groups[0] if len(groups) == 1 else groups)
                    or match.group(0)
                )

            token = self.bot.interface._current_event.set(wrapped_evt)
            try:
                reserved = [await self.get_perm_module(mod), wrapped_evt]
                if extra_args:
                    reserved.extend(extra_args)
                await self._invoke_validated(
                    func=func,
                    reserved_args=reserved,
                    reserved_count=reserved_count,
                    raw_input=raw_input,
                )
            finally:
                self.bot.interface._current_event.reset(token)
        except ValidationError as e:
            msg = self._extract_validation_message(e)
            if reply_on_validation_error:
                await wrapped_evt.reply(f"❌ <b>Validation:</b> <code>{msg}</code>")
            else:
                logger.trace(
                    f"Validation skipped '{func.__name__}' "
                    f"of module '{mod.name}': {msg}"
                )
        except Exception as e:
            logger.exception(
                f"Error in '{func.__name__}' "
                f"of module '{mod.name}': {e}"
            )
