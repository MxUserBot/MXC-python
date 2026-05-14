from enum import Enum as _Enum

from mautrix.types import EventType

from .emoji import EmojiButton
from .handler import InterceptHandler
from .media import DownloadMeta, Image, Audio, Video, Document, Sticker, Media, AnyMedia


class MsgType(str, _Enum):
    TEXT = "m.text"
    IMAGE = "m.image"
    VIDEO = "m.video"
    AUDIO = "m.audio"
    FILE = "m.file"
    EMOTE = "m.emote"
    NOTICE = "m.notice"
    STICKER = "m.sticker"
    REACTION = "m.reaction"
    POLL_START = "org.matrix.msc3381.poll.start"
    POLL_RESPONSE = "org.matrix.msc3381.poll.response"
    POLL_END = "org.matrix.msc3381.poll.end"
    MEMBER = "m.room.member"
    REDACTION = "m.room.redaction"
    NAME = "m.room.name"
    TOPIC = "m.room.topic"
    POWER_LEVELS = "m.room.power_levels"
    JOIN_RULES = "m.room.join_rules"
    TOMBSTONE = "m.room.tombstone"
    CREATE = "m.room.create"
    ENCRYPTION = "m.room.encryption"
    PINNED_EVENTS = "m.room.pinned_events"
    SERVER_ACL = "m.room.server_acl"


POLL_START = EventType.find(MsgType.POLL_START)
POLL_RESPONSE = EventType.find(MsgType.POLL_RESPONSE)
POLL_END = EventType.find(MsgType.POLL_END)

__all__ = [
    "InterceptHandler",
    "Media",
    "Image",
    "Audio",
    "Video",
    "Document",
    "Sticker",
    "AnyMedia",
    "DownloadMeta",
    "MsgType",
    "POLL_START",
    "POLL_RESPONSE",
    "POLL_END",
]
