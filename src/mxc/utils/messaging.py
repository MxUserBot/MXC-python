# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

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
    reply_to: str | None = None,
    reply_markup: Any = None,
    emoji_map: dict | None = None,
    mentions: dict | None = None,
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
                from .keyboard import attach_keyboard
                await attach_keyboard(mx, room_id, edit_id or result, reply_markup, target_event)
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

    if reply_to:
        content.set_reply(reply_to)

    if mentions:
        content["m.mentions"] = mentions

    res = await mx.client.send_message_event(
        room_id=room_id,
        event_type=EventType.ROOM_MESSAGE,
        content=content,
        txn_id=kwargs.get("txn_id"),
    )

    ignore_ids = getattr(mx, '_ignore_ids', None) or getattr(getattr(mx, '_bot', None), '_ignore_ids', None)
    if ignore_ids is not None:
        if edit_id:
            ignore_ids.add(edit_id)
            if res:
                ignore_ids.add(res)
        else:
            ignore_ids.add(res)

    if reply_markup:
        from .keyboard import attach_keyboard
        await attach_keyboard(mx, room_id, edit_id or res, reply_markup, target_event)

    return edit_id or res


async def create_room(
    mx,
    name: str,
    is_direct: bool = False,
    invitees: list[str] | None = None,
    avatar_url: str | None = None,
    topic: str | None = None,
    power_level_override: dict | None = None,
) -> str:
    initial_state = []
    if avatar_url:
        initial_state.append({
            "type": "m.room.avatar",
            "content": {"url": avatar_url},
        })

    kwargs = dict(
        name=name,
        is_direct=is_direct,
        invitees=invitees or [],
        initial_state=initial_state or None,
        topic=topic,
    )
    if power_level_override:
        kwargs["power_level_override"] = power_level_override
    room_id = await mx.client.create_room(**kwargs)

    return str(room_id)


async def join_room(mx, room_id: str) -> None:
    await mx.client.join_room(room_id)


async def pin(mx, room_id: str, event_id: str | None = None) -> bool:
    if event_id:
        try:
            try:
                current = await mx.client.get_state_event(room_id, EventType.ROOM_PINNED_EVENTS)
                pinned = current.get("pinned", []) if current else []
            except Exception:
                pinned = []
            if event_id not in pinned:
                pinned.append(event_id)
            await mx.client.send_state_event(
                room_id=room_id,
                event_type=EventType.ROOM_PINNED_EVENTS,
                content={"pinned": pinned},
                state_key="",
            )
            return True
        except Exception as e:
            logger.error(f"Failed to pin event {event_id}: {e}")
            return False

    try:
        await mx.client.set_room_tag(room_id, "m.favorite", RoomTagInfo(order=0.0))
        return True
    except Exception as e:
        logger.error(f"Failed to favorite room {room_id}: {e}")
        return False


async def set_room_nick(mx, room_id: str, displayname: str) -> None:
    """Set your display name in a specific room."""
    await mx.client.api.request(
        "PUT",
        f"/_matrix/client/v3/rooms/{room_id}/state/m.room.member/{mx.client.mxid}",
        content={"membership": "join", "displayname": displayname},
    )


async def forward(mx, event, room_id: str) -> str:
    """Forward a message event to another room. Returns the new event_id."""
    content = event.content
    text = getattr(content, "body", "") or ""

    file_ = getattr(content, "file", None)
    url = getattr(content, "url", None)
    if file_ or url:
        from ..types.media import Audio, Document, Image, Video, DownloadMeta
        from .media import download

        d = await download(mx, DownloadMeta(url=content))
        if d:
            mime = (d.mimetype or "").lower()
            info = getattr(content, "info", None)
            w = getattr(info, "width", None) if info else None
            h = getattr(info, "height", None) if info else None

            if mime.startswith("image/"):
                media = Image(url=d.url, mimetype=d.mimetype, filename=d.filename, w=w, h=h)
            elif mime.startswith("video/"):
                media = Video(url=d.url, mimetype=d.mimetype, filename=d.filename, w=w, h=h)
            elif mime.startswith("audio/"):
                media = Audio(url=d.url, mimetype=d.mimetype, filename=d.filename)
            else:
                media = Document(url=d.url, mimetype=d.mimetype, filename=d.filename)

            return await answer(mx, text=text, media=media, room_id=room_id)

    return await answer(mx, text=text, room_id=room_id)


async def get_power_levels(mx, room_id: str):
    """Get power levels for a room. Returns PowerLevelStateEventContent or None."""
    try:
        return await mx.client.state_store.get_power_levels(room_id)
    except Exception:
        pass
    try:
        raw = await mx.client.api.request(
            "GET", f"/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels"
        )
        from mautrix.types import PowerLevelStateEventContent
        pl = PowerLevelStateEventContent.deserialize(raw)
        await mx.client.state_store.set_power_levels(room_id, pl)
        return pl
    except Exception:
        return None


async def set_power_level(mx, room_id: str, user_id: str, level: int) -> bool:
    """Set power level for a user in a room."""
    try:
        pl = await get_power_levels(mx, room_id)
        if not pl:
            return False
        pl.set_user_level(user_id, level)
        await mx.client.send_state_event(room_id, EventType.ROOM_POWER_LEVELS, pl)
        await mx.client.state_store.set_power_levels(room_id, pl)
        return True
    except Exception as e:
        logger.error(f"Failed to set power level for {user_id} in {room_id}: {e}")
        return False


async def get_room(mx, room_id: str) -> dict | None:
    """Get room info including members, power levels, and state."""
    try:
        members = await mx.client.get_joined_members(room_id)
        pl = await get_power_levels(mx, room_id)
        return {
            "room_id": room_id,
            "members": members,
            "power_levels": pl,
            "admins": [
                uid for uid in members
                if pl and pl.users.get(uid, pl.users_default) >= (pl.kick or 50)
            ] if pl else [],
        }
    except Exception as e:
        logger.error(f"Failed to get room info for {room_id}: {e}")
        return None


async def unpin(mx, room_id: str, event_id: str | None = None) -> bool:
    if event_id:
        try:
            try:
                current = await mx.client.get_state_event(room_id, EventType.ROOM_PINNED_EVENTS)
                pinned = current.get("pinned", []) if current else []
            except Exception:
                pinned = []
            if event_id in pinned:
                pinned.remove(event_id)
            await mx.client.send_state_event(
                room_id=room_id,
                event_type=EventType.ROOM_PINNED_EVENTS,
                content={"pinned": pinned},
                state_key="",
            )
            return True
        except Exception as e:
            logger.error(f"Failed to unpin event {event_id}: {e}")
            return False

    try:
        await mx.client.remove_room_tag(room_id, "m.favorite")
        return True
    except Exception as e:
        logger.error(f"Failed to unfavorite room {room_id}: {e}")
        return False
