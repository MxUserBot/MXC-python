# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC-python
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from ..types import EmojiButton


EmojiCallback = Callable[["EmojiCallbackContext"], Awaitable[None] | None]
EmojiButtonsInput = Mapping[str, Any] | Sequence[EmojiButton | str | tuple]


def _normalize_emoji_buttons(
    buttons: EmojiButtonsInput,
) -> dict[str, EmojiButton]:
    if isinstance(buttons, Mapping):
        return {emoji: EmojiButton(emoji=emoji, data=data) for emoji, data in buttons.items()}
    return {btn.emoji: btn for btn in buttons}


@dataclass
class EmojiKeyBoard:
    rows: Sequence[Union[EmojiButton, Sequence[EmojiButton]]]
    callback: EmojiCallback

    ttl: int = 0
    allowed_senders: Union[str, Sequence[str], None] = None
    remove_clicked: bool = True
    keep_reactions: bool = True
    preserve_order: bool = True
    newest_first: bool = True
    allow_sudo: bool = True
    data: dict = field(default_factory=dict)


async def attach_keyboard(mx, room_id: str, message_id: str, markup: EmojiKeyBoard, event: Any):
    from .emoji import emoji_callback

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
        ttl=markup.ttl,
        remove_clicked=markup.remove_clicked,
        keep_reactions=markup.keep_reactions,
        preserve_order=markup.preserve_order,
        newest_first=markup.newest_first,
        allow_sudo=markup.allow_sudo,
        data=markup.data,
    )
