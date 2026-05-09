import asyncio
from typing import Optional

from mautrix.types import Event, EventType, MessageEvent

_CRYPTO_BACKGROUND_TASKS = set()


async def fetch_room_messages(
    mx,
    room_id: str,
    limit: int = 100,
    from_token: str = None,
    direction: str = "b",
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


async def get_reply_event(mx, event: MessageEvent) -> Optional[MessageEvent]:
    relates = getattr(event.content, "relates_to", None) or getattr(event.content, "_relates_to", None)
    if not relates:
        return None

    reply_to = getattr(relates, "in_reply_to", None)
    if not reply_to or not reply_to.event_id:
        return None

    try:
        replied_event = await mx.client.get_event(event.room_id, reply_to.event_id)
        await decrypt_event(mx, replied_event)

        try:
            url = (
                f"{mx.client.api.base_url}/_matrix/client/v1/rooms/"
                f"{event.room_id}/relations/{reply_to.event_id}/m.replace"
            )
            headers = {"Authorization": f"Bearer {mx.client.api.token}"}

            async with mx.client.api.session.get(url, headers=headers) as res:
                if res.status == 200:
                    data = await res.json()
                    chunks = data.get("chunk", [])
                    if chunks:
                        latest_dict = max(chunks, key=lambda x: x.get("origin_server_ts", 0))
                        latest_edit_event = MessageEvent.deserialize(latest_dict)
                        await decrypt_event(mx, latest_edit_event)

                        content = latest_edit_event.content
                        new_content = getattr(content, "new_content", None)
                        if not new_content and isinstance(content, dict):
                            new_content = content.get("m.new_content")

                        if new_content:
                            new_body = getattr(new_content, "body", None) or new_content.get("body")
                            if new_body:
                                replied_event.content.body = new_body
        except Exception:
            pass

        return replied_event
    except Exception as e:
        raise e


async def get_reply_text(mx, event: MessageEvent) -> str | None | bool:
    reply_to = getattr(event.content, "relates_to", None)
    if not reply_to or getattr(reply_to, "in_reply_to", None) is None:
        return False

    try:
        replied_event = await get_reply_event(mx, event)
        if not replied_event:
            raise Exception("Событие не найдено")
    except Exception as e:
        from .messaging import answer

        await answer(mx, text=f"❌ <b>Не удалось скачать сообщение:</b> {e}", event=event)
        return None

    return getattr(replied_event.content, "body", "")


async def get_context_events(
    mx,
    room_id: str,
    event_id: str,
    limit: int = 10
) -> list[MessageEvent]:
    response = await mx.client.api.request(
        "GET",
        f"/_matrix/client/v3/rooms/{room_id}/context/{event_id}",
        query_params={"limit": str(limit)},
    )

    events = []

    for evt_dict in response.get("events_before", []):
        evt = MessageEvent.deserialize(evt_dict)
        await decrypt_event(mx, evt)
        events.append(evt)

    event_dict = response.get("event")
    if event_dict:
        evt = MessageEvent.deserialize(event_dict)
        await decrypt_event(mx, evt)
        events.append(evt)

    return events


async def is_dm(mx, room_id: str) -> bool:
    direct_data = await mx.client.get_account_data(EventType.DIRECT)
    return any(room_id in rooms for rooms in direct_data.values())
