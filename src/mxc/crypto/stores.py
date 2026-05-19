# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC-python
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import base64
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from loguru import logger
from mautrix.client.state_store import MemoryStateStore, SyncStore
from mautrix.crypto import InboundGroupSession, OlmAccount, OutboundGroupSession, RatchetSafety, Session
from mautrix.crypto.store import CryptoStore
from mautrix.errors import GroupSessionWithheldError
from mautrix.types import (
    CrossSigner,
    CrossSigningUsage,
    DeviceID,
    DeviceIdentity,
    EventID,
    IdentityKey,
    Member,
    MemberStateEventContent,
    Membership,
    PowerLevelStateEventContent,
    RoomEncryptionStateEventContent,
    RoomID,
    RoomKeyWithheldCode,
    SessionID,
    SigningKey,
    StateEvent,
    SyncToken,
    TOFUSigningKey,
    TrustState,
    UserID,
)

from mxc.database import Database

_MISSING = object()


def _key_part(value: Any) -> str:
    raw = str(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pickle_to_json(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return base64.b64encode(value).decode("ascii")


def _pickle_from_json(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _dt_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_json(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _td_to_ms(value: timedelta | None) -> int | None:
    return int(value.total_seconds() * 1000) if value else None


def _td_from_ms(value: int | None) -> timedelta | None:
    return timedelta(milliseconds=value) if value is not None else None


def _device_to_json(identity: DeviceIdentity) -> dict[str, Any]:
    return {
        "user_id": str(identity.user_id),
        "device_id": str(identity.device_id),
        "identity_key": str(identity.identity_key),
        "signing_key": str(identity.signing_key),
        "trust": int(identity.trust),
        "deleted": bool(identity.deleted),
        "name": identity.name,
    }


def _device_from_json(data: dict[str, Any]) -> DeviceIdentity:
    return DeviceIdentity(
        user_id=UserID(data["user_id"]),
        device_id=DeviceID(data["device_id"]),
        identity_key=IdentityKey(data["identity_key"]),
        signing_key=SigningKey(data["signing_key"]),
        trust=TrustState(data["trust"]),
        deleted=bool(data["deleted"]),
        name=data.get("name"),
    )


def _usage_value(usage: CrossSigningUsage | str) -> str:
    return usage.value if hasattr(usage, "value") else str(usage)


class RocksCryptoStore(CryptoStore, SyncStore):
    account_id: str
    pickle_key: str

    def __init__(self, db: Database, account_id: str, pickle_key: str) -> None:
        self.db = db
        self.account_id = account_id
        self.pickle_key = pickle_key
        self.owner = f"crypto:{account_id}"

        self._sync_token: SyncToken | None = None
        self._device_id: DeviceID | None = None
        self._account: OlmAccount | None = None
        self._olm_cache: dict[IdentityKey, dict[SessionID, Session]] = defaultdict(dict)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def flush(self) -> None:
        await self.db.flush()

    async def delete(self) -> None:
        await self.db.delete_prefix(self.owner)
        self._sync_token = None
        self._device_id = None
        self._account = None
        self._olm_cache.clear()

    async def get_device_id(self) -> DeviceID | None:
        meta = await self._get_meta()
        device_id = meta.get("device_id")
        if device_id:
            self._device_id = DeviceID(device_id)
        return self._device_id

    async def put_device_id(self, device_id: DeviceID) -> None:
        meta = await self._get_meta()
        meta["device_id"] = str(device_id)
        await self._put_meta(meta)
        self._device_id = device_id

    async def put_next_batch(self, next_batch: SyncToken) -> None:
        meta = await self._get_meta()
        meta["sync_token"] = str(next_batch) if next_batch is not None else None
        await self._put_meta(meta)
        self._sync_token = next_batch

    async def get_next_batch(self) -> SyncToken:
        if self._sync_token is None:
            meta = await self._get_meta()
            sync_token = meta.get("sync_token")
            if sync_token:
                self._sync_token = SyncToken(sync_token)
        return self._sync_token

    async def put_account(self, account: OlmAccount) -> None:
        meta = await self._get_meta()
        meta.update(
            {
                "account": _pickle_to_json(account.pickle(self.pickle_key)),
                "shared": account.shared,
                "device_id": str(self._device_id or meta.get("device_id") or ""),
                "sync_token": str(self._sync_token or meta.get("sync_token") or ""),
            }
        )
        await self._put_meta(meta)
        self._account = account

    async def get_account(self) -> OlmAccount | None:
        if self._account is not None:
            return self._account
        meta = await self._get_meta()
        if "account" not in meta:
            return None
        self._account = OlmAccount.from_pickle(
            _pickle_from_json(meta["account"]),
            passphrase=self.pickle_key,
            shared=bool(meta.get("shared")),
        )
        return self._account

    async def has_session(self, key: IdentityKey) -> bool:
        if self._olm_cache[key]:
            return True
        items = await self.db.items_with_prefix(self.owner, self._olm_prefix(key))
        return bool(items)

    async def get_sessions(self, key: IdentityKey) -> list[Session]:
        items = await self.db.items_with_prefix(self.owner, self._olm_prefix(key))
        sessions = [self._session_from_json(key, data) for _, data in items]
        sessions.sort(key=lambda sess: sess.last_decrypted, reverse=True)
        return sessions

    async def get_latest_session(self, key: IdentityKey) -> Session | None:
        sessions = await self.get_sessions(key)
        return sessions[0] if sessions else None

    async def add_session(self, key: IdentityKey, session: Session) -> None:
        self._olm_cache[key][SessionID(session.id)] = session
        await self._set(self._olm_key(key, SessionID(session.id)), self._session_to_json(key, session))

    async def update_session(self, key: IdentityKey, session: Session) -> None:
        self._olm_cache[key][SessionID(session.id)] = session
        await self._set(self._olm_key(key, SessionID(session.id)), self._session_to_json(key, session))

    async def put_group_session(
        self,
        room_id: RoomID,
        sender_key: IdentityKey,
        session_id: SessionID,
        session: InboundGroupSession,
    ) -> None:
        await self._set(
            self._inbound_key(room_id, session_id),
            {
                "session_id": str(session_id),
                "sender_key": str(sender_key),
                "signing_key": str(session.signing_key),
                "room_id": str(room_id),
                "session": _pickle_to_json(session.pickle(self.pickle_key)),
                "forwarding_chain": [str(item) for item in session.forwarding_chain],
                "ratchet_safety": session.ratchet_safety.serialize(),
                "received_at": _dt_to_json(session.received_at),
                "max_age": _td_to_ms(session.max_age),
                "max_messages": session.max_messages,
                "is_scheduled": bool(session.is_scheduled),
                "withheld_code": None,
                "withheld_reason": None,
            },
        )

    async def get_group_session(
        self, room_id: RoomID, session_id: SessionID
    ) -> InboundGroupSession | None:
        data = await self._get(self._inbound_key(room_id, session_id))
        if data is None:
            return None
        if data.get("withheld_code"):
            raise GroupSessionWithheldError(session_id, data["withheld_code"])
        if not data.get("session"):
            return None
        return InboundGroupSession.from_pickle(
            _pickle_from_json(data["session"]),
            passphrase=self.pickle_key,
            signing_key=SigningKey(data["signing_key"]),
            sender_key=IdentityKey(data["sender_key"]),
            room_id=RoomID(data["room_id"]),
            forwarding_chain=[IdentityKey(item) for item in data.get("forwarding_chain", [])],
            ratchet_safety=RatchetSafety.deserialize(data.get("ratchet_safety") or {}),
            received_at=_dt_from_json(data.get("received_at")),
            max_age=_td_from_ms(data.get("max_age")),
            max_messages=data.get("max_messages"),
            is_scheduled=bool(data.get("is_scheduled")),
        )

    async def redact_group_session(
        self, room_id: RoomID, session_id: SessionID, reason: str
    ) -> None:
        key = self._inbound_key(room_id, session_id)
        data = await self._get(key)
        if not data or not data.get("session"):
            return
        await self._redact_inbound(key, data, reason)

    async def redact_group_sessions(
        self, room_id: RoomID | None, sender_key: IdentityKey | None, reason: str
    ) -> list[SessionID]:
        if not room_id and not sender_key:
            raise ValueError("Either room_id or sender_key must be provided")
        deleted = []
        for key, data in await self.db.items_with_prefix(self.owner, "inbound:"):
            if room_id and data.get("room_id") != str(room_id):
                continue
            if sender_key and data.get("sender_key") != str(sender_key):
                continue
            if not data.get("session") or data.get("is_scheduled") or not data.get("received_at"):
                continue
            await self._redact_inbound(key, data, reason)
            deleted.append(SessionID(data["session_id"]))
        return deleted

    async def redact_expired_group_sessions(self) -> list[SessionID]:
        now = datetime.utcnow()
        deleted = []
        for key, data in await self.db.items_with_prefix(self.owner, "inbound:"):
            received_at = _dt_from_json(data.get("received_at"))
            max_age = _td_from_ms(data.get("max_age"))
            if (
                data.get("session")
                and not data.get("is_scheduled")
                and received_at
                and max_age
                and received_at + max_age * 2 < now
            ):
                await self._redact_inbound(key, data, "expired")
                deleted.append(SessionID(data["session_id"]))
        return deleted

    async def redact_outdated_group_sessions(self) -> list[SessionID]:
        deleted = []
        for key, data in await self.db.items_with_prefix(self.owner, "inbound:"):
            if data.get("session") and not data.get("received_at"):
                await self._redact_inbound(key, data, "outdated")
                deleted.append(SessionID(data["session_id"]))
        return deleted

    async def has_group_session(self, room_id: RoomID, session_id: SessionID) -> bool:
        data = await self._get(self._inbound_key(room_id, session_id))
        return bool(data and data.get("session"))

    async def add_outbound_group_session(self, session: OutboundGroupSession) -> None:
        await self._set(self._outbound_key(session.room_id), self._outbound_to_json(session))

    async def update_outbound_group_session(self, session: OutboundGroupSession) -> None:
        await self._set(self._outbound_key(session.room_id), self._outbound_to_json(session))

    async def get_outbound_group_session(self, room_id: RoomID) -> OutboundGroupSession | None:
        data = await self._get(self._outbound_key(room_id))
        if not data:
            return None
        return OutboundGroupSession.from_pickle(
            _pickle_from_json(data["session"]),
            passphrase=self.pickle_key,
            room_id=RoomID(data["room_id"]),
            shared=bool(data["shared"]),
            max_messages=int(data["max_messages"]),
            message_count=int(data["message_count"]),
            max_age=_td_from_ms(data["max_age"]),
            use_time=_dt_from_json(data["last_used"]),
            creation_time=_dt_from_json(data["created_at"]),
        )

    async def remove_outbound_group_session(self, room_id: RoomID) -> None:
        await self.db.delete(self.owner, self._outbound_key(room_id))

    async def remove_outbound_group_sessions(self, rooms: list[RoomID]) -> None:
        for room_id in rooms:
            await self.remove_outbound_group_session(room_id)

    async def validate_message_index(
        self,
        sender_key: IdentityKey,
        session_id: SessionID,
        event_id: EventID,
        index: int,
        timestamp: int,
    ) -> bool:
        key = self._message_index_key(sender_key, session_id, index)
        data = await self._get(key)
        if data is None:
            await self._set(key, {"event_id": str(event_id), "timestamp": int(timestamp)})
            return True
        return data.get("event_id") == str(event_id) and data.get("timestamp") == int(timestamp)

    async def get_devices(self, user_id: UserID) -> dict[DeviceID, DeviceIdentity] | None:
        data = await self._get(self._devices_key(user_id), default=_MISSING)
        if data is _MISSING:
            return None
        return {
            DeviceID(device_id): _device_from_json(identity)
            for device_id, identity in data.items()
        }

    async def get_device(self, user_id: UserID, device_id: DeviceID) -> DeviceIdentity | None:
        devices = await self.get_devices(user_id)
        return devices.get(device_id) if devices is not None else None

    async def find_device_by_key(
        self, user_id: UserID, identity_key: IdentityKey
    ) -> DeviceIdentity | None:
        devices = await self.get_devices(user_id)
        if not devices:
            return None
        for device in devices.values():
            if device.identity_key == identity_key:
                return device
        return None

    async def put_devices(self, user_id: UserID, devices: dict[DeviceID, DeviceIdentity]) -> None:
        await self._set(
            self._devices_key(user_id),
            {str(device_id): _device_to_json(identity) for device_id, identity in devices.items()},
        )

    async def filter_tracked_users(self, users: list[UserID]) -> list[UserID]:
        tracked = []
        for user_id in users:
            if await self._get(self._devices_key(user_id), default=_MISSING) is not _MISSING:
                tracked.append(user_id)
        return tracked

    async def put_cross_signing_key(
        self, user_id: UserID, usage: CrossSigningUsage | str, key: SigningKey
    ) -> None:
        current = await self._get(self._cross_key(user_id, usage))
        usage_value = _usage_value(usage)
        await self._set(
            self._cross_key(user_id, usage),
            {
                "usage": usage_value,
                "key": str(key),
                "first": current.get("first") if current else str(key),
            },
        )

    async def get_cross_signing_keys(
        self, user_id: UserID
    ) -> dict[CrossSigningUsage, TOFUSigningKey]:
        result = {}
        for _, data in await self.db.items_with_prefix(self.owner, self._cross_prefix(user_id)):
            if "usage" not in data:
                continue
            usage = CrossSigningUsage(data["usage"])
            result[usage] = TOFUSigningKey(
                key=SigningKey(data["key"]),
                first=SigningKey(data["first"]),
            )
        return result

    async def put_signature(
        self, target: CrossSigner, signer: CrossSigner, signature: str
    ) -> None:
        await self._set(
            self._signature_key(target, signer),
            {
                "target_user_id": str(target.user_id),
                "target_key": str(target.key),
                "signer_user_id": str(signer.user_id),
                "signer_key": str(signer.key),
                "signature": signature,
            },
        )

    async def is_key_signed_by(self, target: CrossSigner, signer: CrossSigner) -> bool:
        return await self._get(self._signature_key(target, signer), default=_MISSING) is not _MISSING

    async def drop_signatures_by_key(self, signer: CrossSigner) -> int:
        deleted = 0
        for key, data in await self.db.items_with_prefix(self.owner, "signature:"):
            if data.get("signer_user_id") == str(signer.user_id) and data.get("signer_key") == str(signer.key):
                await self.db.delete(self.owner, key)
                deleted += 1
        return deleted

    async def _get(self, key: str, default: Any = None) -> Any:
        return await self.db.get(self.owner, key, default)

    async def _set(self, key: str, value: Any) -> None:
        if not await self.db.set(self.owner, key, value):
            logger.error(f"Failed to persist crypto store key {key}")

    async def _get_meta(self) -> dict[str, Any]:
        return await self._get("account", default={}) or {}

    async def _put_meta(self, meta: dict[str, Any]) -> None:
        await self._set("account", meta)

    def _session_to_json(self, key: IdentityKey, session: Session) -> dict[str, Any]:
        return {
            "session_id": str(session.id),
            "sender_key": str(key),
            "session": _pickle_to_json(session.pickle(self.pickle_key)),
            "created_at": _dt_to_json(session.creation_time),
            "last_encrypted": _dt_to_json(session.last_encrypted),
            "last_decrypted": _dt_to_json(session.last_decrypted),
        }

    def _session_from_json(self, key: IdentityKey, data: dict[str, Any]) -> Session:
        session_id = SessionID(data["session_id"])
        try:
            return self._olm_cache[key][session_id]
        except KeyError:
            session = Session.from_pickle(
                _pickle_from_json(data["session"]),
                passphrase=self.pickle_key,
                creation_time=_dt_from_json(data["created_at"]),
                last_encrypted=_dt_from_json(data.get("last_encrypted")),
                last_decrypted=_dt_from_json(data.get("last_decrypted")),
            )
            self._olm_cache[key][session_id] = session
            return session

    def _outbound_to_json(self, session: OutboundGroupSession) -> dict[str, Any]:
        return {
            "room_id": str(session.room_id),
            "session_id": str(session.id),
            "session": _pickle_to_json(session.pickle(self.pickle_key)),
            "shared": bool(session.shared),
            "max_messages": session.max_messages,
            "message_count": session.message_count,
            "max_age": _td_to_ms(session.max_age),
            "created_at": _dt_to_json(session.creation_time),
            "last_used": _dt_to_json(session.use_time),
        }

    async def _redact_inbound(self, key: str, data: dict[str, Any], reason: str) -> None:
        data["withheld_code"] = RoomKeyWithheldCode.BEEPER_REDACTED.value
        data["withheld_reason"] = f"Session redacted: {reason}"
        data["session"] = None
        data["forwarding_chain"] = None
        await self._set(key, data)

    def _olm_prefix(self, key: IdentityKey) -> str:
        return f"olm:{_key_part(key)}:"

    def _olm_key(self, key: IdentityKey, session_id: SessionID) -> str:
        return f"{self._olm_prefix(key)}{_key_part(session_id)}"

    def _inbound_key(self, room_id: RoomID, session_id: SessionID) -> str:
        return f"inbound:{_key_part(room_id)}:{_key_part(session_id)}"

    def _outbound_key(self, room_id: RoomID) -> str:
        return f"outbound:{_key_part(room_id)}"

    def _message_index_key(
        self, sender_key: IdentityKey, session_id: SessionID, index: int
    ) -> str:
        return f"message-index:{_key_part(sender_key)}:{_key_part(session_id)}:{index}"

    def _devices_key(self, user_id: UserID) -> str:
        return f"devices:{_key_part(user_id)}"

    def _cross_prefix(self, user_id: UserID) -> str:
        return f"cross-key:{_key_part(user_id)}:"

    def _cross_key(self, user_id: UserID, usage: CrossSigningUsage | str) -> str:
        return f"{self._cross_prefix(user_id)}{_key_part(_usage_value(usage))}"

    def _signature_key(self, target: CrossSigner, signer: CrossSigner) -> str:
        return (
            f"signature:{_key_part(target.user_id)}:{_key_part(target.key)}:"
            f"{_key_part(signer.user_id)}:{_key_part(signer.key)}"
        )


class RocksCryptoStateStore(MemoryStateStore):
    def __init__(self, db: Database, account_id: str) -> None:
        super().__init__()
        self.db = db
        self.owner = f"crypto-state:{account_id}"

    async def load(self) -> None:
        data = await self.db.get(self.owner, "snapshot")
        if not data:
            return
        data.setdefault("members", {})
        data.setdefault("full_member_list", {})
        data.setdefault("power_levels", {})
        data.setdefault("encryption", {})
        data.setdefault("create", {})
        self.deserialize(data)

    async def flush(self) -> None:
        await self._persist()

    async def find_shared_rooms(self, user_id: UserID) -> list[RoomID]:
        return [
            RoomID(room_id)
            for room_id, members in self.members.items()
            if user_id in members and self.encryption.get(room_id) is not None
        ]

    async def set_member(
        self, room_id: RoomID, user_id: UserID, member: Member | MemberStateEventContent
    ) -> None:
        await super().set_member(room_id, user_id, member)
        await self._persist()

    async def set_membership(
        self, room_id: RoomID, user_id: UserID, membership: Membership
    ) -> None:
        await super().set_membership(room_id, user_id, membership)
        await self._persist()

    async def set_members(
        self,
        room_id: RoomID,
        members: dict[UserID, Member | MemberStateEventContent],
        only_membership: Membership | None = None,
    ) -> None:
        await super().set_members(room_id, members, only_membership)
        await self._persist()

    async def set_power_levels(
        self, room_id: RoomID, content: PowerLevelStateEventContent | dict[str, Any]
    ) -> None:
        await super().set_power_levels(room_id, content)
        await self._persist()

    async def set_create(self, event: StateEvent | dict[str, Any]) -> None:
        await super().set_create(event)
        await self._persist()

    async def set_encryption_info(
        self, room_id: RoomID, content: RoomEncryptionStateEventContent | dict[str, Any]
    ) -> None:
        await super().set_encryption_info(room_id, content)
        await self._persist()

    async def _persist(self) -> None:
        if not await self.db.set(self.owner, "snapshot", self.serialize()):
            logger.error("Failed to persist crypto state store")

    async def get(self, key: str, default: Any = None) -> Any:
        return await self.db.get(self.owner, key, default)

    async def set(self, key: str, value: Any) -> None:
        await self.db.set(self.owner, key, value)
