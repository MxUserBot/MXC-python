import io
from dataclasses import dataclass
from typing import Union

from loguru import logger
from mautrix.types import (
    AudioInfo,
    FileInfo,
    ImageInfo,
    MediaMessageEventContent,
    MessageType,
    VideoInfo,
    Format,
)
from PIL import Image as PILImage


@dataclass
class Media:
    url: str | bytes
    mimetype: str | None = None
    filename: str | None = None
    size: int | None = None
    caption: str | None = None

    _default_mime: str = "application/octet-stream"
    _msgtype: MessageType = MessageType.FILE
    _info_class: type = FileInfo

    def __post_init__(self):
        if not self.mimetype:
            self.mimetype = self._default_mime
        if not self.filename:
            ext = self.mimetype.split('/')[-1].split(';')[0]
            self.filename = f"file.{ext}"

    def _get_info_kwargs(self) -> dict:
        return {"mimetype": self.mimetype, "size": self.size}

    async def to_mautrix_content(self, text: str = None, html: bool = True) -> MediaMessageEventContent:
        body_text = self.filename or "file"
        caption_text = text or self.caption
        kwargs = {k: v for k, v in self._get_info_kwargs().items() if v is not None}

        content = MediaMessageEventContent(
            msgtype=self._msgtype,
            body=body_text,
            info=self._info_class(**kwargs) if self._info_class else None
        )

        if html and caption_text:
            content.format = Format.HTML
            content.formatted_body = caption_text

        return content


@dataclass
class Image(Media):
    w: int | None = None
    h: int | None = None
    _default_mime: str = "image/png"
    _msgtype: MessageType = MessageType.IMAGE
    _info_class: type = ImageInfo

    def __post_init__(self):
        super().__post_init__()
        if isinstance(self.url, bytes) and (not self.w or not self.h):
            try:
                with PILImage.open(io.BytesIO(self.url)) as img:
                    self.w, self.h = img.size
            except Exception as e:
                logger.warning(f"Failed to auto-get image size: {e}")

    def _get_info_kwargs(self) -> dict:
        info = super()._get_info_kwargs()
        info.update({"width": self.w, "height": self.h})
        return info


@dataclass
class Audio(Media):
    duration: int | None = None
    _default_mime: str = "audio/mpeg"
    _msgtype: MessageType = MessageType.AUDIO
    _info_class: type = AudioInfo
    def _get_info_kwargs(self) -> dict:
        info = super()._get_info_kwargs()
        info.update({"duration": self.duration})
        return info


@dataclass
class Video(Media):
    w: int | None = None
    h: int | None = None
    duration: int | None = None
    _default_mime: str = "video/mp4"
    _msgtype: MessageType = MessageType.VIDEO
    _info_class: type = VideoInfo
    def _get_info_kwargs(self) -> dict:
        info = super()._get_info_kwargs()
        info.update({"width": self.w, "height": self.h, "duration": self.duration})
        return info


@dataclass
class Document(Media):
    pass


@dataclass
class Sticker(Image):
    _default_mime: str = "image/webp"
    _msgtype: MessageType = MessageType.STICKER
    _info_class: type = ImageInfo


AnyMedia = Union[Media, Image, Audio, Video, Sticker]
