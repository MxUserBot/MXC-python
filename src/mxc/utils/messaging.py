from typing import Any

from loguru import logger
from mautrix.types import EventType, Format, MessageType, RoomTagInfo, TextMessageEventContent


async def answer(
    mx: Any,
    text: str | None = None,
    media=None,
    html: bool = True,
    room_id: str | None = None,
    event: Any = None,
    edit_id: str | None = "-1",
    reply_markup: Any = None,
    emoji_map: dict | None = None,
    **kwargs,
) -> str:
    if media is None:
        media = kwargs.pop("image", None)

    target_event = event or mx._current_event.get(None)

    room_id = room_id or (target_event.room_id if target_event else None)

    if not room_id:
        logger.error("utils.answer() called without room_id")
        return ""

    if edit_id == "-1":
        edit_id = (
            target_event.event_id
            if target_event and target_event.sender == mx.client.mxid
            else None
        )

    if media:
        from ..types.media import Audio, Document, Image, Sticker, Video
        from .media import send_audio, send_document, send_image, send_sticker, send_video

        if isinstance(media, Sticker):
            result = await send_sticker(mx, room_id, media, text, html, edit_id, **kwargs)
        elif isinstance(media, Image):
            result = await send_image(mx, room_id, media, text, html, edit_id, **kwargs)
        elif isinstance(media, Audio):
            result = await send_audio(mx, room_id, media, text, html, edit_id, **kwargs)
        elif isinstance(media, Video):
            result = await send_video(mx, room_id, media, text, html, edit_id, **kwargs)
        elif isinstance(media, Document):
            result = await send_document(mx, room_id, media, text, html, edit_id, **kwargs)
        else:
            logger.warning(f"Unknown media type: {type(media)}. Sending text.")
            result = None

        if result is not None:
            if reply_markup:
                from .emoji import attach_keyboard
                await attach_keyboard(mx, room_id, result, reply_markup, target_event)
            return result

    body_text = text or ""
    content = TextMessageEventContent(msgtype=MessageType.TEXT, body=body_text)

    if emoji_map and body_text:
        from .emoji import render_emojis
        content.format = Format.HTML
        content.formatted_body = render_emojis(body_text, emoji_map)
    elif html and body_text:
        content.format = Format.HTML
        content.formatted_body = body_text

    if edit_id:
        content.set_edit(edit_id)

    res = await mx.client.send_message_event(
        room_id=room_id,
        event_type=EventType.ROOM_MESSAGE,
        content=content,
        txn_id=kwargs.get("txn_id"),
    )

    sent_id = edit_id if edit_id else res

    if reply_markup:
        from .emoji import attach_keyboard
        await attach_keyboard(mx, room_id, sent_id, reply_markup, target_event)

    return sent_id


async def pin_room(mx, room_id) -> bool:
    await mx.client.set_room_tag(room_id, "m.favorite", RoomTagInfo(order=0.0))
    return True


async def unpin_room(mx, room_id) -> bool:
    await mx.client.remove_room_tag(room_id, "m.favorite")
    return True


async def pin(mx, room_id: str, event_id: str, unpin: bool = False):
    try:
        try:
            current_state = await mx.client.get_state_event(room_id, EventType.ROOM_PINNED_EVENTS)
            pinned = current_state.get("pinned", []) if current_state else []
        except Exception:
            pinned = []

        if unpin:
            if event_id in pinned:
                pinned.remove(event_id)
        elif event_id not in pinned:
            pinned.append(event_id)

        return await mx.client.send_state_event(
            room_id=room_id,
            event_type=EventType.ROOM_PINNED_EVENTS,
            content={"pinned": pinned},
            state_key="",
        )
    except Exception as e:
        logger.error(f"Failed to pin/unpin {event_id}: {e}")
        return None
