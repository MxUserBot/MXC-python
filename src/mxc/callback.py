import asyncio
import inspect
from typing import Any

from loguru import logger
from mautrix.types import EventType, MessageEvent
from pydantic import ConfigDict, ValidationError, validate_call

pd_config = ConfigDict(arbitrary_types_allowed=True)


class BaseCallBack:
    def __init__(self, bot):
        self.bot = bot

    async def get_perm_module(self, mod):
        return self.bot.interface if getattr(mod, "_is_core", False) else self.bot.interface

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

    async def _dispatch_event(self, event_type: EventType, evt: Any) -> None:
        from mxc.utils import dispatch_emoji_callback

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
                asyncio.create_task(self._safe_run_handler(mod, handler, wrapped_evt))

    def _get_handler_params(self, func: callable, reserved_count: int) -> list[inspect.Parameter]:
        orig_f = getattr(func, "__func__", func)
        sig = inspect.signature(orig_f)
        return list(sig.parameters.values())[reserved_count:]

    def _extract_validation_message(self, error: ValidationError) -> str:
        try:
            first_error = error.errors(include_url=False)[0]
            return str(first_error.get("msg", "Validation error"))
        except Exception:
            return "Validation error"

    def _build_handler_kwargs(
        self,
        params: list[inspect.Parameter],
        raw_input: Any = None,
        reply_text: str | None = None,
    ) -> dict[str, Any]:
        if not params:
            return {}

        kwargs: dict[str, Any] = {}
        source = raw_input

        if len(params) == 1:
            if source in (None, "") and reply_text:
                source = reply_text

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

        if reply_text:
            mandatory = [p for p in params if p.default in (inspect.Parameter.empty, None)]
            for p in reversed(mandatory):
                if p.name not in kwargs:
                    kwargs[p.name] = reply_text
                    break

        return kwargs

    async def _invoke_validated(
        self,
        func: callable,
        reserved_args: list[Any],
        reserved_count: int,
        raw_input: Any = None,
        reply_text: str | None = None,
    ) -> None:
        params = self._get_handler_params(func, reserved_count)
        kwargs = self._build_handler_kwargs(
            params=params,
            raw_input=raw_input,
            reply_text=reply_text,
        )

        v_func = validate_call(func, config=pd_config)
        await v_func(*reserved_args, **kwargs)

    async def _safe_run_handler(self, mod: Any, func: callable, wrapped_evt: Any) -> None:
        try:
            raw_input = getattr(getattr(wrapped_evt, "content", None), "body", None)
            if raw_input is None:
                raw_input = getattr(wrapped_evt, "content", None)

            token = self.bot.interface._current_event.set(wrapped_evt)
            try:
                await self._invoke_validated(
                    func=func,
                    reserved_args=[await self.get_perm_module(mod), wrapped_evt],
                    reserved_count=3,
                    raw_input=raw_input,
                )
            finally:
                self.bot.interface._current_event.reset(token)
        except ValidationError as e:
            logger.trace(
                f"Validation skipped event handler '{func.__name__}' "
                f"of module '{mod.name}': {self._extract_validation_message(e)}"
            )
        except Exception as e:
            logger.exception(
                f"Error in event handler '{func.__name__}' "
                f"of module '{mod.name}': {e}"
            )

    async def _safe_run_watcher(self, mod: Any, func: callable, wrapped_evt: Any, match: Any = None) -> None:
        try:
            raw_input = getattr(getattr(wrapped_evt, "content", None), "body", None)
            if match:
                groups = match.groups()
                raw_input = (
                    match.groupdict()
                    or (groups[0] if len(groups) == 1 else groups)
                    or match.group(0)
                )

            token = self.bot.interface._current_event.set(wrapped_evt)
            try:
                await self._invoke_validated(
                    func=func,
                    reserved_args=[await self.get_perm_module(mod), wrapped_evt],
                    reserved_count=3,
                    raw_input=raw_input,
                )
            finally:
                self.bot.interface._current_event.reset(token)
        except ValidationError as e:
            logger.trace(
                f"Validation skipped watcher '{func.__name__}' "
                f"of module '{mod.name}': {self._extract_validation_message(e)}"
            )
        except Exception as e:
            logger.exception(f"Error in watcher '{func.__name__}' of module '{mod.name}': {e}")

    async def _safe_run_state_handler(self, mod: Any, func: callable, wrapped_evt: Any, ctx: Any) -> None:
        try:
            raw_input = getattr(getattr(wrapped_evt, "content", None), "body", None)

            token = self.bot.interface._current_event.set(wrapped_evt)
            try:
                await self._invoke_validated(
                    func=func,
                    reserved_args=[await self.get_perm_module(mod), wrapped_evt, ctx],
                    reserved_count=4,
                    raw_input=raw_input,
                )
            finally:
                self.bot.interface._current_event.reset(token)
        except ValidationError as e:
            msg = self._extract_validation_message(e)
            await wrapped_evt.reply(f"❌ <b>Validation:</b> <code>{msg}</code>")
        except Exception as e:
            logger.exception(
                f"Error in state handler '{func.__name__}' "
                f"of module '{mod.name}': {e}"
            )
