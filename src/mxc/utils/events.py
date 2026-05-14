# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
from typing import Optional

from mautrix.types import Event, EventType, MessageEvent

from mxc.types import MsgType

_CRYPTO_BACKGROUND_TASKS = set()

_MESSAGE_TYPES = frozenset({
    MsgType.TEXT, MsgType.IMAGE, MsgType.VIDEO, MsgType.AUDIO,
    MsgType.FILE, MsgType.EMOTE, MsgType.NOTICE, MsgType.STICKER,
})

_MSGTYPE_MAP = {
    "m.text": MsgType.TEXT,
    "m.image": MsgType.IMAGE,
    "m.video": MsgType.VIDEO,
    "m.audio": MsgType.AUDIO,
    "m.file": MsgType.FILE,
    "m.emote": MsgType.EMOTE,
    "m.notice": MsgType.NOTICE,
}


def _event_matches(evt: dict, types: set[MsgType]) -> bool:
    evt_type = evt.get("type")

    if evt_type == "m.room.encrypted":
        return bool(types & _MESSAGE_TYPES)

    if evt_type == "m.sticker":
        return MsgType.STICKER in types

    if evt_type == "m.room.message":
        msgtype = evt.get("content", {}).get("msgtype")
        if msgtype:
            return _MSGTYPE_MAP.get(msgtype) in types
        return False

    try:
        return MsgType(evt_type) in types
    except ValueError:
        return False


async def fetch_room_messages(
    mx,
    room_id: str,
    limit: int = 100,
    from_token: str = None,
    direction: str = "b",
    types: Optional[set[MsgType]] = None,
) -> dict:
    query_params = {
        "dir": direction,
        "limit": str(limit),
    }

    if from_token:
        query_params["from"] = from_token

    response = await mx.client.api.request(
        "GET",
        f"/_matrix/client/v3/rooms/{room_id}/messages",
        query_params=query_params,
    )

    if "chunk" in response:
        for i, evt_dict in enumerate(response["chunk"]):
            if evt_dict.get("type") == "m.room.encrypted":
                try:
                    evt_obj = Event.deserialize(evt_dict)
                    await decrypt_event(mx, evt_obj)
                    response["chunk"][i] = evt_obj.serialize()
                except Exception:
                    pass

        if types is not None:
            if not isinstance(types, set):
                types = set(types)
            filtered = []
            for evt in response["chunk"]:
                if evt.get("unsigned", {}).get("redacted_because"):
                    continue
                if _event_matches(evt, types):
                    filtered.append(evt)
            response["chunk"] = filtered

    return response


async def decrypt_event(mx, event, context_event: MessageEvent = None) -> bool:
    if event.type != EventType.ROOM_ENCRYPTED:
        return True

    try:
        decrypted = await mx.client.crypto.decrypt_megolm_event(event)
        event.content = decrypted.content
        event.type = decrypted.type
        return True
    except Exception:
        pass

    users_to_ask = {mx.client.mxid, event.sender}
    from_devices = {}
    for user_id in users_to_ask:
        devices = await mx.client.crypto.crypto_store.get_devices(user_id)
        if devices:
            from_devices[user_id] = {
                dev_id: dev.identity_key
                for dev_id, dev in devices.items()
            }

    if from_devices:
        task = asyncio.create_task(
            mx.client.crypto.request_room_key(
                room_id=event.room_id,
                sender_key=event.content.sender_key,
                session_id=event.content.session_id,
                from_devices=from_devices,
            )
        )
        _CRYPTO_BACKGROUND_TASKS.add(task)
        task.add_done_callback(_CRYPTO_BACKGROUND_TASKS.discard)

        for _ in range(1):
            await asyncio.sleep(0.1)
            try:
                decrypted = await mx.client.crypto.decrypt_megolm_event(event)
                event.content = decrypted.content
                event.type = decrypted.type
                return True
            except Exception:
                continue

        return False


async def _apply_latest_edit(mx, room_id: str, event_id: str, target: MessageEvent) -> None:
    try:
        try:
            data = await mx.client.api.request(
                "GET",
                f"/_matrix/client/v1/rooms/{room_id}/relations/{event_id}/m.replace"
            )
        except Exception as e:
            raise e


        chunks = data.get("chunk", [])
        if not chunks:
            return

        latest_dict = max(chunks, key=lambda x: x.get("origin_server_ts", 0))
        latest_edit_event = Event.deserialize(latest_dict)
        latest_edit_event.room_id = room_id

        await decrypt_event(mx, latest_edit_event)

        content = latest_edit_event.content
        if not content:
            return

        new_content = getattr(content, "new_content", None)
        c_dict = content.serialize() if hasattr(content, "serialize") else (content if isinstance(content, dict) else {})
        m_new_content = c_dict.get("m.new_content") or {}
        
        new_body = getattr(new_content, "body", None) if new_content else m_new_content.get("body")
        
        if new_body is not None:
            target.content.body = new_body
            new_formatted = getattr(new_content, "formatted_body", None) if new_content else m_new_content.get("formatted_body")
            if hasattr(target.content, "formatted_body"):
                target.content.formatted_body = new_formatted
        else:
            fallback_body = getattr(content, "body", None) or c_dict.get("body")
            if fallback_body is not None:
                if fallback_body.startswith(" * "):
                    fallback_body = fallback_body[3:]
                target.content.body = fallback_body

    except Exception:
        pass

async def get_reply_event(mx, event: MessageEvent, apply_edit: bool = True) -> Optional[MessageEvent]:
    relates = getattr(event.content, "relates_to", None) or getattr(event.content, "_relates_to", None)
    if not relates:
        return None

    if getattr(relates, "rel_type", None) == "m.replace":
        try:
            original = await mx.client.get_event(event.room_id, relates.event_id)
            await decrypt_event(mx, original)
            if apply_edit:
                await _apply_latest_edit(mx, event.room_id, relates.event_id, original)
            orig_relates = getattr(original.content, "relates_to", None) or getattr(original.content, "_relates_to", None)
            if orig_relates and getattr(orig_relates, "in_reply_to", None) and orig_relates.in_reply_to.event_id:
                replied = await mx.client.get_event(event.room_id, orig_relates.in_reply_to.event_id)
                await decrypt_event(mx, replied)
                if apply_edit:
                    await _apply_latest_edit(mx, event.room_id, orig_relates.in_reply_to.event_id, replied)
                return replied
        except Exception:
            pass
        return None

    reply_to = getattr(relates, "in_reply_to", None)
    if not reply_to or not reply_to.event_id:
        return None

    try:
        replied_event = await mx.client.get_event(event.room_id, reply_to.event_id)
        await decrypt_event(mx, replied_event)

        if apply_edit:
            await _apply_latest_edit(mx, event.room_id, reply_to.event_id, replied_event)

        return replied_event
    except Exception as e:
        raise e


async def get_reply_text(mx, event: MessageEvent, apply_edit: bool = True) -> str | None | bool:
    reply_to = getattr(event.content, "relates_to", None)
    if not reply_to or getattr(reply_to, "in_reply_to", None) is None:
        return False

    try:
        replied_event = await get_reply_event(mx, event, apply_edit=apply_edit)
        if not replied_event:
            return None
        if replied_event.type == EventType.ROOM_ENCRYPTED:
            return None
    except Exception:
        return None

    return getattr(replied_event.content, "body", "")


async def get_context_events(
    mx,
    room_id: str,
    event_id: str,
    limit: int = 10,
    apply_edit: bool = True,
) -> list[MessageEvent]:
    response = await mx.client.api.request(
        "GET",
        f"/_matrix/client/v3/rooms/{room_id}/context/{event_id}",
        query_params={"limit": str(limit)},
    )

    events = []

    for evt_dict in response.get("events_before", []):
        evt = Event.deserialize(evt_dict)
        await decrypt_event(mx, evt)
        if apply_edit:
            await _apply_latest_edit(mx, room_id, evt.event_id, evt)
        events.append(evt)

    events.reverse()

    event_dict = response.get("event")
    if event_dict:
        evt = Event.deserialize(event_dict)
        await decrypt_event(mx, evt)
        if apply_edit:
            await _apply_latest_edit(mx, room_id, evt.event_id, evt)
        events.append(evt)

    return events


async def is_dm(mx, room_id: str) -> bool:
    direct_data = await mx.client.get_account_data(EventType.DIRECT)
    return any(room_id in rooms for rooms in direct_data.values())
