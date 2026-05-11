from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class EmojiButton:
    emoji: str
    data: Any = None
