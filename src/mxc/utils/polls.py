import secrets
import string
from typing import Any, Optional

from mautrix.types import Event, EventType, MessageEvent

from .events import decrypt_event

POLL_START_TYPE = "org.matrix.msc3381.poll.start"
POLL_RESPONSE_TYPE = "org.matrix.msc3381.poll.response"
POLL_END_TYPE = "org.matrix.msc3381.poll.end"
POLL_DISCLOSED = "org.matrix.msc3381.poll.disclosed"
POLL_UNDISCLOSED = "org.matrix.msc3381.poll.undisclosed"


def generate_answer_id() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))


def make_poll_start(
    question: str,
    answers: list[str],
    kind: str = POLL_DISCLOSED,
    max_selections: int = 1,
) -> dict:
    lines = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(answers))
    fallback = f"{question}\n{lines}"

    return {
        "type": POLL_START_TYPE,
        "content": {
            "org.matrix.msc1767.text": fallback,
            POLL_START_TYPE: {
                "kind": kind,
                "max_selections": max_selections,
                "question": {
                    "org.matrix.msc1767.text": question,
                },
                "answers": [
                    {
                        "id": generate_answer_id(),
                        "org.matrix.msc1767.text": answer,
                    }
                    for answer in answers
                ],
            },
        },
    }


def parse_poll_start(event) -> Optional[dict]:
    content = getattr(event, "content", None) or {}
    raw = content.get("content") or content
    poll = raw.get(POLL_START_TYPE) or {}
    if not poll or not poll.get("answers"):
        return None
    return {
        "question": (
            poll.get("question", {}).get("org.matrix.msc1767.text")
            or raw.get("org.matrix.msc1767.text", "")
        ),
        "kind": poll.get("kind", POLL_UNDISCLOSED),
        "max_selections": poll.get("max_selections", 1),
        "answers": [
            {"id": a["id"], "text": a.get("org.matrix.msc1767.text", "")}
            for a in poll["answers"]
        ],
    }


def make_poll_response(poll_event_id: str, selections: list[str]) -> dict:
    return {
        "type": POLL_RESPONSE_TYPE,
        "content": {
            "m.relates_to": {
                "rel_type": "m.reference",
                "event_id": poll_event_id,
            },
            POLL_RESPONSE_TYPE: {
                "answers": selections,
            },
        },
    }


def parse_poll_response(event) -> Optional[list[str]]:
    content = getattr(event, "content", None) or {}
    raw = content.get("content") or content
    relates_to = raw.get("m.relates_to") or {}
    if relates_to.get("rel_type") != "m.reference":
        return None
    response = raw.get(POLL_RESPONSE_TYPE) or {}
    return response.get("answers") or []


def make_poll_end(poll_event_id: str, fallback_text: str) -> dict:
    return {
        "type": POLL_END_TYPE,
        "content": {
            "m.relates_to": {
                "rel_type": "m.reference",
                "event_id": poll_event_id,
            },
            "org.matrix.msc1767.text": fallback_text,
            POLL_END_TYPE: {},
        },
    }


def parse_poll_end(event) -> Optional[str]:
    content = getattr(event, "content", None) or {}
    raw = content.get("content") or content
    relates_to = raw.get("m.relates_to") or {}
    if relates_to.get("rel_type") != "m.reference":
        return None
    return relates_to.get("event_id")


def is_poll_start(event) -> bool:
    t = getattr(event, "type", None)
    if isinstance(t, EventType):
        t = t.t
    return t == POLL_START_TYPE


def is_poll_response(event) -> bool:
    t = getattr(event, "type", None)
    if isinstance(t, EventType):
        t = t.t
    return t == POLL_RESPONSE_TYPE


def is_poll_end(event) -> bool:
    t = getattr(event, "type", None)
    if isinstance(t, EventType):
        t = t.t
    return t == POLL_END_TYPE


async def get_poll_responses(
    mx,
    room_id: str,
    poll_event_id: str,
) -> list[MessageEvent]:
    try:
        response = await mx.client.api.request(
            "GET",
            f"/_matrix/client/v3/rooms/{room_id}/relations/{poll_event_id}/m.reference/{POLL_RESPONSE_TYPE}",
        )
    except Exception:
        return []

    events = []
    for chunk in response.get("chunk", []):
        if chunk.get("type") != POLL_RESPONSE_TYPE:
            continue
        evt = MessageEvent.deserialize(chunk)
        await decrypt_event(mx, evt)
        events.append(evt)

    events.sort(key=lambda e: getattr(e, "timestamp", 0) or 0)
    return events


def tally_poll(
    responses: list[MessageEvent],
    answers: list[dict],
    max_selections: int = 1,
) -> dict[str, int]:
    tally = {a["id"]: 0 for a in answers}
    seen_users: dict[str, int] = {}

    for evt in responses:
        selections = parse_poll_response(evt)
        if selections is None:
            continue
        sender = getattr(evt, "sender", None)
        ts = getattr(evt, "timestamp", 0) or 0
        if sender and (sender not in seen_users or ts > seen_users[sender]):
            seen_users[sender] = ts

    latest = {}
    for evt in responses:
        sender = getattr(evt, "sender", None)
        ts = getattr(evt, "timestamp", 0) or 0
        if sender and seen_users.get(sender) == ts:
            selections = parse_poll_response(evt) or []
            for sel in selections[:max_selections]:
                if sel in tally:
                    tally[sel] += 1
            latest[sender] = selections[:max_selections]

    return tally, latest
