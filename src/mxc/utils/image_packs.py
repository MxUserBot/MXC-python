# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import html as html_mod
import re
from typing import Optional

from mautrix.types import EventType, Event

from .common import request

IMAGE_PACK_STABLE = "m.room.image_pack"
IMAGE_PACK_ROOMS_STABLE = "m.image_pack.rooms"
IMAGE_PACK_UNSTABLE = "im.ponies.room_emotes"
IMAGE_PACK_ROOMS_UNSTABLE = "im.ponies.emote_rooms"
EMOTE_ATTR = "data-mx-emoticon"
PACK_TYPES = {IMAGE_PACK_STABLE, IMAGE_PACK_UNSTABLE}
PACK_ROOMS_TYPES = {IMAGE_PACK_ROOMS_STABLE, IMAGE_PACK_ROOMS_UNSTABLE}
USAGE_EMOTICON = "emoticon"
USAGE_STICKER = "sticker"

SHORTCODE_RE = re.compile(r"^[a-zA-Z0-9-_]+$")


def validate_shortcode(code: str) -> bool:
    return bool(SHORTCODE_RE.match(code)) and len(code.encode("utf-8")) <= 100


def sanitize_state_key(name: str) -> str:
    key = name.lower().replace(" ", "_").replace("/", "_")
    key = re.sub(r"[^a-z0-9_-]", "", key)
    return key or "pack"


def make_pack_content(
    display_name: str,
    images: Optional[dict[str, dict]] = None,
    usage: Optional[list[str]] = None,
    attribution: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> dict:
    pack = {"display_name": display_name}
    if avatar_url:
        pack["avatar_url"] = avatar_url
    if usage:
        pack["usage"] = usage
    if attribution:
        pack["attribution"] = attribution

    content = {
        "images": images or {},
        "pack": pack,
    }
    return content


def make_image_object(
    url: str,
    body: Optional[str] = None,
    info: Optional[dict] = None,
) -> dict:
    obj = {"url": url}
    if body:
        obj["body"] = body
    if info:
        obj["info"] = info
    return obj


def make_emote_html(
    shortcode: str,
    mxc_url: str,
    body: Optional[str] = None,
    height: int = 32,
) -> str:
    escaped_url = html_mod.escape(mxc_url, quote=True)
    alt = html_mod.escape(body or shortcode, quote=True)
    title = html_mod.escape(shortcode, quote=True)
    return (
        f'<img data-mx-emoticon src="{escaped_url}" '
        f'alt="{alt}" title="{title}" height="{height}" />'
    )


def parse_pack_content(content: dict) -> Optional[dict]:
    if not content:
        return None

    inner = content.get("content") or content
    images = inner.get("images") or {}
    pack = inner.get("pack") or {}

    return {
        "display_name": pack.get("display_name", ""),
        "avatar_url": pack.get("avatar_url"),
        "usage": pack.get("usage", []),
        "attribution": pack.get("attribution"),
        "images": {
            code: {
                "url": img.get("url", ""),
                "body": img.get("body"),
                "info": img.get("info"),
            }
            for code, img in images.items()
            if img.get("url")
        },
    }


def strip_shortcode(raw: str) -> str:
    return raw.strip().strip(":").strip()


async def get_room_packs(mx, room_id: str) -> list[dict]:
    try:
        state = await mx.client.api.request(
            "GET", f"/_matrix/client/v3/rooms/{room_id}/state"
        )
    except Exception:
        return []

    results = []
    for entry in state:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type", "")
        if etype not in PACK_TYPES:
            continue
        content = entry.get("content", {})
        parsed = parse_pack_content(content)
        if not parsed:
            continue

        evt = Event.deserialize(entry)
        results.append({
            "state_key": entry.get("state_key", ""),
            "event_id": getattr(evt, "event_id", None) or entry.get("event_id", ""),
            "sender": getattr(evt, "sender", None) or entry.get("sender", ""),
            **parsed,
        })
    return results


async def get_global_pack_refs(mx) -> dict[str, dict[str, dict]]:
    for atype in PACK_ROOMS_TYPES:
        try:
            data = await mx.client.get_account_data(EventType.find(atype))
            rooms = {}
            if isinstance(data, dict):
                raw = data.get("content") or data
                raw_rooms = raw.get("rooms") or raw.get("m.image_pack.rooms") or {}
                if isinstance(raw_rooms, dict):
                    for rid, packs in raw_rooms.items():
                        if isinstance(packs, dict):
                            rooms[rid] = packs
            if rooms:
                return rooms
        except Exception:
            continue
    return {}


async def get_all_packs(mx, room_id: str) -> list[dict]:
    seen = set()
    packs = []

    room_packs = await get_room_packs(mx, room_id)
    for p in room_packs:
        key = (room_id, p["state_key"])
        if key not in seen:
            seen.add(key)
            p["_source"] = "room"
            packs.append(p)

    global_refs = await get_global_pack_refs(mx)
    for ref_rid, ref_packs in global_refs.items():
        if ref_rid == room_id:
            continue
        for state_key in ref_packs:
            try:
                content = await mx.client.get_state_event(
                    EventType.find(IMAGE_PACK_STABLE),
                    state_key=state_key if state_key else "",
                )
                if content:
                    parsed = parse_pack_content(content)
                    if parsed:
                        key = (ref_rid, state_key)
                        if key not in seen:
                            seen.add(key)
                            packs.append({"_source": "global", **parsed})
            except Exception:
                try:
                    content = await mx.client.get_state_event(
                        EventType.find(IMAGE_PACK_UNSTABLE),
                        state_key=state_key if state_key else "",
                    )
                    if content:
                        parsed = parse_pack_content(content)
                        if parsed:
                            key = (ref_rid, state_key)
                            if key not in seen:
                                seen.add(key)
                                packs.append({"_source": "global", **parsed})
                except Exception:
                    continue

    return packs


async def resolve_shortcode(
    mx, room_id: str, shortcode: str
) -> Optional[dict]:
    shortcode = strip_shortcode(shortcode)
    if not shortcode:
        return None

    all_packs = await get_all_packs(mx, room_id)
    for pack in all_packs:
        images = pack.get("images", {})
        if shortcode in images:
            img = images[shortcode]
            usage = pack.get("usage", [])
            return {
                "shortcode": shortcode,
                "mxc_url": img["url"],
                "body": img.get("body"),
                "info": img.get("info"),
                "pack_name": pack.get("display_name", ""),
                "usage": usage,
                "_source": pack.get("_source", "room"),
            }
    return None


async def download_and_upload_media(mx, event) -> Optional[str]:
    from mxc.types.media import DownloadMeta
    from .media import download

    try:
        data, filename, mimetype, _ = await download(mx, meta=DownloadMeta(url=event))
        mxc = await mx.client.upload_media(data, mime_type=mimetype, filename=filename)
        return mxc
    except Exception:
        return None


async def set_global_pack_ref(
    mx, room_id: str, state_key: str, add: bool = True
) -> bool:
    refs = await get_global_pack_refs(mx)

    if add:
        if room_id not in refs:
            refs[room_id] = {}
        if state_key not in refs[room_id]:
            refs[room_id][state_key] = {}
    else:
        if room_id in refs:
            refs[room_id].pop(state_key, None)
            if not refs[room_id]:
                del refs[room_id]

    try:
        for atype in PACK_ROOMS_TYPES:
            try:
                etype = EventType.find(atype)
                await mx.client.set_account_data(etype, {"rooms": refs})
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def emoji_for_usage(usage_list: list[str]) -> str:
    if USAGE_STICKER in usage_list and USAGE_EMOTICON not in usage_list:
        return "🖼️"
    return "😃"


async def get_state_event_by_id(
    mx, room_id: str, event_id: str
) -> Optional[dict]:
    try:
        response = await mx.client.api.request(
            "GET",
            f"/_matrix/client/v3/rooms/{room_id}/event/{event_id}",
        )
        if isinstance(response, dict):
            etype = response.get("type", "")
            if etype in PACK_TYPES:
                return response
    except Exception:
        pass
    return None


def parse_quoted(text: str) -> list[str]:
    parts = []
    buf: list[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                parts.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


async def find_pack_by_name(mx, room_id: str, name: str) -> Optional[dict]:
    packs = await get_room_packs(mx, room_id)
    name_lower = name.lower()
    for p in packs:
        dn = (p.get("display_name") or "").lower()
        sk = (p.get("state_key") or "").lower()
        if name_lower in (dn, sk):
            return p
    for p in packs:
        dn = (p.get("display_name") or "").lower()
        sk = (p.get("state_key") or "").lower()
        if name_lower in dn or name_lower in sk:
            return p
    return None


async def fetch_pack_state(mx, room_id: str, state_key: str) -> dict:
    for etype_name in PACK_TYPES:
        try:
            etype = EventType.find(etype_name)
            content = await mx.client.get_state_event(etype, state_key=state_key)
            if content:
                if isinstance(content, dict) and "content" in content:
                    content = content["content"]
                return content
        except Exception:
            continue
    return {}
