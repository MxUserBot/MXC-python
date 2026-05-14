# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import io
import uuid
from typing import Any, Optional, Tuple

from mautrix.api import Method
from mautrix.crypto.attachments import decrypt_attachment, encrypt_attachment
from mautrix.types import EncryptedFile, EventType, ThumbnailInfo
from PIL import Image as PILImage

from mxc.types.media import DownloadMeta, Image as MxcImage, Media

from .common import request

RPC_NAMESPACE = "com.ip-logger.msc4320.rpc"


async def _get_media_bytes(mx: Any, media_url: bytes | str) -> bytes:
    if isinstance(media_url, bytes):
        return media_url
    if isinstance(media_url, str):
        if media_url.startswith("http"):
            return await request(media_url, return_type="bytes")
        if media_url.startswith("mxc://"):
            return await mx.client.download_media(media_url)

    raise ValueError(f"Cannot read media bytes from: {type(media_url)}")


async def upload(mx: Any, media: Any, mime_type: str | None = None, **kwargs) -> str:
    if isinstance(media, Media):
        if isinstance(media.url, str) and media.url.startswith("mxc://"):
            return media.url
        data = await _get_media_bytes(mx, media.url)
        return str(await mx.client.upload_media(data, mime_type=media.mimetype, filename=media.filename, **kwargs))

    if isinstance(media, bytes):
        return str(await mx.client.upload_media(media, mime_type=mime_type or "application/octet-stream", **kwargs))

    if isinstance(media, str):
        if media.startswith("mxc://"):
            return media
        data = await request(media, return_type="bytes")
        return str(await mx.client.upload_media(data, mime_type=mime_type or "application/octet-stream", **kwargs))

    raise ValueError(f"Cannot upload: {type(media)}")


async def download(mx: Any, meta: DownloadMeta = None) -> Any:
    if meta is None:
        meta = DownloadMeta()

    source = meta.url
    if source is None:
        raise ValueError("DownloadMeta.url is required")

    if isinstance(source, Media):
        return await _get_media_bytes(mx, source.url) if not meta.thumbnail else None

    if isinstance(source, (str, bytes)):
        return await _get_media_bytes(mx, source)

    if hasattr(source, "content"):
        from .events import decrypt_event

        g = await decrypt_event(mx, source)
        if not g:
            raise 
        content = source.content
    else:
        content = source

    if meta.thumbnail:
        info = getattr(content, "info", None)
        if not info:
            return None
        thumb_file = getattr(info, "thumbnail_file", None)
        if thumb_file:
            ciphertext = await mx.client.download_media(thumb_file.url)
            return decrypt_attachment(
                ciphertext,
                thumb_file.key.key,
                thumb_file.hashes.get("sha256"),
                thumb_file.iv,
            )
        thumb_url = getattr(info, "thumbnail_url", None)
        if thumb_url:
            return await mx.client.download_media(str(thumb_url))
        return None

    filename = (
        getattr(content, "filename", None)
        or getattr(content, "body", None)
        or f"matrix_{uuid.uuid4().hex[:8]}"
    )
    info = getattr(content, "info", None)
    mimetype = getattr(info, "mimetype", None) or "application/octet-stream"
    size = getattr(info, "size", None)

    encrypted_file = getattr(content, "file", None)
    if encrypted_file:
        ciphertext = await mx.client.download_media(encrypted_file.url)
        data = decrypt_attachment(
            ciphertext,
            encrypted_file.key.key,
            encrypted_file.hashes.get("sha256"),
            encrypted_file.iv,
        )
        return data, filename, mimetype, size or len(data)

    url = getattr(content, "url", None)
    if not url:
        raise ValueError("Matrix message has no downloadable media URL")

    data = await mx.client.download_media(url)
    return data, filename, mimetype, size or len(data)


async def encrypt(
    mx: Any,
    room_id: str,
    file_bytes: bytes,
    mime_type: str,
    filename: str | None = None,
) -> Tuple[str | None, EncryptedFile | None]:
    is_enc = await mx.client.state_store.is_encrypted(room_id) if getattr(mx.client, "crypto", None) else False

    if is_enc:
        await mx.client.crypto.wait_group_session_share(room_id)
        enc_data, file_info = encrypt_attachment(file_bytes)
        file_info.url = await mx.client.upload_media(
            enc_data,
            mime_type="application/octet-stream",
            filename=filename,
        )
        return None, file_info

    url = await mx.client.upload_media(
        file_bytes,
        mime_type=mime_type,
        filename=filename,
    )
    return url, None


async def send_image(mx: Any, room_id: str, media, text: str | None = None, html: bool = True, edit_id: str | None = None, **kwargs) -> str:
    file_bytes = await _get_media_bytes(mx, media.url)
    content = await media.to_mautrix_content(text=text, html=html)

    if not content.info.size:
        content.info.size = len(file_bytes)

    thumb_bytes = None
    tw, th = 0, 0
    w, h = media.w, media.h

    try:
        with PILImage.open(io.BytesIO(file_bytes)) as img_obj:
            if not w or not h:
                content.info.width, content.info.height = img_obj.size

            content.info["org.matrix.msc4230.is_animated"] = getattr(img_obj, "is_animated", False)

            thumb_img = img_obj.copy()
            thumb_img.thumbnail((400, 400))
            tw, th = thumb_img.size
            t_io = io.BytesIO()
            thumb_img.save(t_io, format="PNG")
            thumb_bytes = t_io.getvalue()
    except Exception as e:
        raise e

    filename = media.filename or f"image_{uuid.uuid4().hex[:4]}.png"

    content.url, content.file = await encrypt(mx, room_id, file_bytes, content.info.mimetype, filename)

    if thumb_bytes:
        t_url, t_file = await encrypt(mx, room_id, thumb_bytes, "image/png")
        content.info.thumbnail_url = t_url
        content.info.thumbnail_file = t_file
        content.info.thumbnail_info = ThumbnailInfo(mimetype="image/png", size=len(thumb_bytes), width=tw, height=th)

    if "relates_to" in kwargs: content.relates_to = kwargs.pop("relates_to")
    if edit_id: content.set_edit(edit_id)

    return await mx.client.send_message_event(room_id, EventType.ROOM_MESSAGE, content, **kwargs)


async def send_video(mx: Any, room_id: str, media, text: str | None = None, html: bool = True, edit_id: str | None = None, **kwargs) -> str:
    file_bytes = await _get_media_bytes(mx, media.url)
    content = await media.to_mautrix_content(text=text, html=html)
    if not content.info.size:
        content.info.size = len(file_bytes)

    filename = media.filename or f"video_{uuid.uuid4().hex[:4]}.mp4"
    content.url, content.file = await encrypt(mx, room_id, file_bytes, content.info.mimetype, filename)

    if "relates_to" in kwargs:
        content.relates_to = kwargs.pop("relates_to")
    if edit_id:
        content.set_edit(edit_id)

    return await mx.client.send_message_event(room_id, EventType.ROOM_MESSAGE, content, **kwargs)


async def send_audio(mx: Any, room_id: str, media, text: str | None = None, html: bool = True, edit_id: str | None = None, **kwargs) -> str:
    file_bytes = await _get_media_bytes(mx, media.url)
    content = await media.to_mautrix_content(text=text, html=html)
    if not content.info.size:
        content.info.size = len(file_bytes)

    filename = media.filename or f"audio_{uuid.uuid4().hex[:4]}.mp3"
    content.url, content.file = await encrypt(mx, room_id, file_bytes, content.info.mimetype, filename)

    if "relates_to" in kwargs:
        content.relates_to = kwargs.pop("relates_to")
    if edit_id:
        content.set_edit(edit_id)

    return await mx.client.send_message_event(room_id, EventType.ROOM_MESSAGE, content, **kwargs)


async def send_document(mx: Any, room_id: str, media, text: str | None = None, html: bool = True, edit_id: str | None = None, **kwargs) -> str:
    file_bytes = await _get_media_bytes(mx, media.url)
    content = await media.to_mautrix_content(text=text, html=html)
    if not content.info.size:
        content.info.size = len(file_bytes)

    filename = media.filename or f"doc_{uuid.uuid4().hex[:4]}.dat"
    content.url, content.file = await encrypt(mx, room_id, file_bytes, content.info.mimetype, filename)

    if "relates_to" in kwargs:
        content.relates_to = kwargs.pop("relates_to")
    if edit_id:
        content.set_edit(edit_id)

    return await mx.client.send_message_event(room_id, EventType.ROOM_MESSAGE, content, **kwargs)


async def send_sticker(mx: Any, room_id: str, media, text: str | None = None, html: bool = True, edit_id: str | None = None, **kwargs) -> str:
    from mautrix.types import EventType

    file_bytes = await _get_media_bytes(mx, media.url)

    body = text or getattr(media, "body", getattr(media, "filename", "sticker"))

    filename = media.filename or f"sticker_{uuid.uuid4().hex[:4]}.webp"

    url, enc_file = await encrypt(mx, room_id, file_bytes, media.mimetype or "image/webp", filename)

    info = {
        "mimetype": media.mimetype or "image/webp",
        "w": media.w,
        "h": media.h,
        "size": len(file_bytes) if not getattr(media, "size", None) else media.size,
    }

    content = {
        "body": body,
        "info": info,
    }

    if url:
        content["url"] = url
    if enc_file:
        content["file"] = enc_file.serialize() if hasattr(enc_file, "serialize") else enc_file

    relates_to = kwargs.pop("relates_to", None)
    if relates_to:
        if hasattr(relates_to, "serialize"):
            content["m.relates_to"] = relates_to.serialize()
        else:
            content["m.relates_to"] = relates_to

    if edit_id:
        new_content = dict(content)
        if "m.relates_to" in new_content:
            del new_content["m.relates_to"]
        content["m.relates_to"] = {
            "rel_type": "m.replace",
            "event_id": edit_id,
            "m.new_content": new_content,
        }

    txn = uuid.uuid4().hex

    resp = await mx.client.api.request(
        Method.PUT,
        f"/_matrix/client/v3/rooms/{room_id}/send/m.sticker/{txn}",
        content=content,
    )

    return resp.get("event_id")


async def set_rpc_media(
    mx,
    artist: str,
    album: str,
    track: str,
    length: int | None = None,
    complete: int | None = None,
    cover_art: str | bytes | None = None,
    player: str | None = None,
    streaming_link: str | None = None,
):
    if cover_art:
        if isinstance(cover_art, bytes):
            cover_art = str(await mx.client.upload_media(cover_art))
        elif isinstance(cover_art, str) and cover_art.startswith(("http://", "https://")):
            img_bytes = await request(cover_art, return_type="bytes")
            cover_art = str(await mx.client.upload_media(img_bytes)) if img_bytes else None

    data = {
        "type": f"{RPC_NAMESPACE}.media",
        "artist": artist,
        "album": album,
        "track": track,
    }

    if length is not None or complete is not None:
        data["progress"] = {}
        if length is not None:
            data["progress"]["length"] = length
        if complete is not None:
            data["progress"]["complete"] = complete

    if cover_art:
        data["cover_art"] = cover_art
    if player:
        data["player"] = player
    if streaming_link:
        data["streaming_link"] = streaming_link

    endpoint = f"_matrix/client/v3/profile/{mx.client.mxid}/{RPC_NAMESPACE}"
    return await mx.client.api.request(Method.PUT, endpoint, content={RPC_NAMESPACE: data})


async def set_rpc_activity(mx, name: str, details: str | None = None, image: str | None = None):
    data = {
        "type": f"{RPC_NAMESPACE}.activity",
        "name": name,
    }

    if details:
        data["details"] = details
    if image:
        data["image"] = image

    endpoint = f"_matrix/client/v3/profile/{mx.client.mxid}/{RPC_NAMESPACE}"
    return await mx.client.api.request(Method.PUT, endpoint, content={RPC_NAMESPACE: data})


async def clear_rpc(mx):
    endpoint = f"_matrix/client/v3/profile/{mx.client.mxid}/{RPC_NAMESPACE}"
    return await mx.client.api.request(Method.DELETE, endpoint)
