# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import base64
import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Tuple

from loguru import logger
from mautrix.client import Client
from mautrix.types import (
    EventType,
    ToDeviceEvent,
    TrustState,
)
from olm.sas import Sas


EMOJI_LIST = [
    "🐕", "🐈", "🦁", "🐴", "🦄", "🐷", "🐘",
    "🐰", "🐼", "🐓", "🐧", "🐢", "🐟",
    "🐙", "🦋", "🌸", "🌲", "🌵", "🍄",
    "🌍", "🌙", "☁️", "🔥", "🍌", "🍎", "🍓",
    "🌽", "🍕", "🎂", "❤️", "😊", "🤖", "🎩",
    "👓", "🔧", "🎅", "👍", "☂️", "⌛",
    "⏰", "🎁", "💡", "📖", "✏️", "📎",
    "✂️", "🔒", "🔑", "🔨", "☎️", "🚩",
    "🚂", "🚲", "✈️", "🚀", "🏆", "⚽",
    "🎸", "🎺", "🔔", "⚓", "🎧", "📁", "📌"
]


class BotSASVerification:
    def __init__(self, client: Client):
        self.client = client
        self.sessions: Dict[str, Dict[str, Any]] = {}
        logger.info("BotSASVerification initialized")
        self.verified_event = asyncio.Event()

    async def prepare_cross_signing(self, recovery_key: str | None = None):
        if self.client.crypto._cross_signing_private_keys:
            logger.info("Cross-signing already loaded")
            return

        if recovery_key:
            await self.client.crypto.verify_with_recovery_key(recovery_key)
            logger.info("Cross-signing keys loaded from recovery key")
            return

        logger.warning("Cross-signing private keys are not loaded")

    def get_canonical_json(self, data: dict) -> str:
        clean_data = {k: v for k, v in data.items() if not k.startswith("__mautrix")}
        return json.dumps(clean_data, separators=(",", ":"), sort_keys=True, ensure_ascii=False)

    async def handle_decrypted_event(self, evt: ToDeviceEvent):
        t = evt.type.t if hasattr(evt.type, "t") else str(evt.type)
        if "m.key.verification." not in t:
            return

        if t == "m.key.verification.request":
            await self.handle_request(evt)
        elif t == "m.key.verification.ready":
            await self.handle_ready(evt)
        elif t == "m.key.verification.start":
            await self.handle_start(evt)
        elif t == "m.key.verification.accept":
            await self.handle_accept(evt)
        elif t == "m.key.verification.key":
            await self.handle_key(evt)
        elif t == "m.key.verification.mac":
            await self.handle_mac(evt)
        elif t == "m.key.verification.cancel":
            s = self.sessions.get(evt.content.get("transaction_id"))
            if s:
                if s.get("result_future") and not s["result_future"].done():
                    s["result_future"].set_result("cancelled")
                if s.get("emoji_future") and not s["emoji_future"].done():
                    s["emoji_future"].set_exception(asyncio.CancelledError())
            self.sessions.pop(evt.content.get("transaction_id"), None)

    async def start_verification(self, user_id: str, device_id: str, room_id: str = None) -> Tuple[List[str], str, asyncio.Future]:
        txn_id = f"v-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        emoji_future = loop.create_future()
        result_future = loop.create_future()
        self.sessions[txn_id] = {
            "sas": Sas(),
            "user_id": user_id,
            "device_id": device_id,
            "role": "alice",
            "room_id": room_id,
            "bot_mac_sent": False,
            "other_mac_received": False,
            "emoji_future": emoji_future,
            "result_future": result_future,
        }
        await self.client.send_to_one_device(
            EventType.find("m.key.verification.request", EventType.Class.TO_DEVICE),
            user_id,
            device_id,
            {
                "from_device": self.client.device_id,
                "methods": ["m.sas.v1"],
                "transaction_id": txn_id,
                "timestamp": int(time.time() * 1000),
            },
        )

        try:
            done, pending = await asyncio.wait(
                [emoji_future, result_future], timeout=120,
                return_when=asyncio.FIRST_COMPLETED
            )
            if result_future in done:
                result = result_future.result()
                self.sessions.pop(txn_id, None)
                if result == "cancelled":
                    raise asyncio.CancelledError("Verification was cancelled")
                raise TimeoutError("Verification timed out")
            emojis = await emoji_future
            return emojis, txn_id, result_future
        except asyncio.TimeoutError:
            if not result_future.done():
                result_future.set_result("timeout")
            self.sessions.pop(txn_id, None)
            raise TimeoutError("Verification timed out waiting for emojis")

    async def handle_ready(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        s = self.sessions.get(txn_id)
        if not s or s["role"] != "alice":
            return

        start_content = {
            "from_device": self.client.device_id,
            "method": "m.sas.v1",
            "key_agreement_protocols": ["curve25519-hkdf-sha256", "curve25519"],
            "hashes": ["sha256"],
            "message_authentication_codes": ["hkdf-hmac-sha256"],
            "short_authentication_string": ["emoji"],
            "transaction_id": txn_id,
        }
        s["start_content"] = start_content

        await self.client.send_to_one_device(
            EventType.find("m.key.verification.start", EventType.Class.TO_DEVICE),
            s["user_id"],
            s["device_id"],
            start_content,
        )

    async def handle_accept(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        s = self.sessions.get(txn_id)
        if not s or s["role"] != "alice":
            return

        await self.client.send_to_one_device(
            EventType.find("m.key.verification.key", EventType.Class.TO_DEVICE),
            s["user_id"],
            s["device_id"],
            {"transaction_id": txn_id, "key": s["sas"].pubkey.replace("=", "")},
        )

    async def handle_request(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        self.sessions[txn_id] = {
            "sas": Sas(),
            "user_id": evt.sender,
            "device_id": evt.content.get("from_device") or evt.sender_device,
            "role": "bob",
            "room_id": None,
            "bot_mac_sent": False,
            "other_mac_received": False,
        }
        await self.client.send_to_one_device(
            EventType.find("m.key.verification.ready", EventType.Class.TO_DEVICE),
            evt.sender,
            self.sessions[txn_id]["device_id"],
            {"transaction_id": txn_id, "methods": ["m.sas.v1"]},
        )

    async def handle_start(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        s = self.sessions.get(txn_id)
        if not s or s["role"] != "bob":
            return

        sas = s["sas"]
        start_content = evt.content.serialize() if hasattr(evt.content, "serialize") else evt.content
        s["start_content"] = start_content

        commitment_str = sas.pubkey.replace("=", "") + self.get_canonical_json(start_content)
        commitment = base64.b64encode(
            hashlib.sha256(commitment_str.encode("utf-8")).digest()
        ).decode().replace("=", "")

        await self.client.send_to_one_device(
            EventType.find("m.key.verification.accept", EventType.Class.TO_DEVICE),
            s["user_id"],
            s["device_id"],
            {
                "transaction_id": txn_id,
                "method": "m.sas.v1",
                "key_agreement_protocol": "curve25519-hkdf-sha256",
                "hash": "sha256",
                "message_authentication_code": "hkdf-hmac-sha256",
                "short_authentication_string": ["emoji"],
                "commitment": commitment,
            },
        )

    async def handle_key(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        s = self.sessions.get(txn_id)
        if not s:
            return

        their_key = evt.content.get("key")
        s["sas"].set_their_pubkey(their_key + "=" * ((4 - len(their_key) % 4) % 4))
        my_pubkey = s["sas"].pubkey.replace("=", "")

        if s["role"] == "bob":
            await self.client.send_to_one_device(
                EventType.find("m.key.verification.key", EventType.Class.TO_DEVICE),
                s["user_id"],
                s["device_id"],
                {"transaction_id": txn_id, "key": my_pubkey},
            )

        a_id, a_dev, a_pk = (
            (self.client.mxid, self.client.device_id, my_pubkey)
            if s["role"] == "alice"
            else (s["user_id"], s["device_id"], their_key)
        )
        b_id, b_dev, b_pk = (
            (s["user_id"], s["device_id"], their_key)
            if s["role"] == "alice"
            else (self.client.mxid, self.client.device_id, my_pubkey)
        )

        sas_info = f"MATRIX_KEY_VERIFICATION_SAS|{a_id}|{a_dev}|{a_pk}|{b_id}|{b_dev}|{b_pk}|{txn_id}"
        sas_bytes = s["sas"].generate_bytes(sas_info.encode("utf-8"), 6)
        val = int.from_bytes(sas_bytes, "big")
        emojis = [
            f"{(val >> (42 - (i * 6))) & 0x3F}:{EMOJI_LIST[(val >> (42 - (i * 6))) & 0x3F]}"
            for i in range(7)
        ]

        s["emojis"] = emojis
        if s.get("emoji_future") and not s["emoji_future"].done():
            s["emoji_future"].set_result(emojis)

        await asyncio.sleep(3)
        if s["role"] == "alice":
            asyncio.create_task(self._send_actual_mac(txn_id))

    async def _send_actual_mac(self, txn_id: str):
        s = self.sessions.get(txn_id)
        if not s or s["bot_mac_sent"]:
            return

        sas = s["sas"]
        user_id, device_id = self.client.mxid, self.client.device_id
        other_user_id, other_device_id = s["user_id"], s["device_id"]

        my_pub_key = self.client.crypto.account.identity_keys["ed25519"].rstrip("=")
        key_id = f"ed25519:{device_id}"

        mac_dict = {
            key_id: sas.calculate_mac(
                my_pub_key,
                "MATRIX_KEY_VERIFICATION_MAC"
                + user_id
                + device_id
                + other_user_id
                + other_device_id
                + txn_id
                + key_id,
            ).rstrip("=")
        }
        keys_mac = sas.calculate_mac(
            key_id,
            "MATRIX_KEY_VERIFICATION_MAC"
            + user_id
            + device_id
            + other_user_id
            + other_device_id
            + txn_id
            + "KEY_IDS",
        ).rstrip("=")

        await self.client.send_to_one_device(
            EventType.find("m.key.verification.mac", EventType.Class.TO_DEVICE),
            other_user_id,
            other_device_id,
            {"transaction_id": txn_id, "mac": mac_dict, "keys": keys_mac},
        )
        s["bot_mac_sent"] = True
        await self._maybe_finish(txn_id)

    async def handle_mac(self, evt: ToDeviceEvent):
        txn_id = evt.content.get("transaction_id")
        s = self.sessions.get(txn_id)
        if not s:
            return

        s["other_mac_received"] = True
        logger.info(f"Received MAC from device {s['device_id']}")
        if not s.get("bot_mac_sent"):
            await self._send_actual_mac(txn_id)
        else:
            await self._maybe_finish(txn_id)

    async def _maybe_finish(self, txn_id: str):
        s = self.sessions.get(txn_id)
        if not (s and s["bot_mac_sent"] and s["other_mac_received"]):
            return

        try:
            await self.client.send_to_one_device(
                EventType.find("m.key.verification.done", EventType.Class.TO_DEVICE),
                s["user_id"], s["device_id"], {"transaction_id": txn_id}
            )

            device = await self.client.crypto.crypto_store.get_device(s["user_id"], s["device_id"])
            if device:
                device.trust = TrustState.VERIFIED
                await self.client.crypto.crypto_store.put_devices(s["user_id"], {s["device_id"]: device})

                if hasattr(self.client.crypto, 'device_list'):
                    self.client.crypto.device_list.set_trust(s["user_id"], s["device_id"], TrustState.VERIFIED)

                try:
                    full_keys = await self.client.crypto._get_full_device_keys(device)
                    from mautrix.crypto.signature import sign_olm
                    signature = sign_olm(full_keys, self.client.crypto.account)

                    if not full_keys.signatures:
                        full_keys.signatures = {}
                    if self.client.mxid not in full_keys.signatures:
                        full_keys.signatures[self.client.mxid] = {}

                    from mautrix.types import CrossSigner, KeyID
                    bot_key_id = KeyID.ed25519(self.client.device_id)
                    full_keys.signatures[self.client.mxid][bot_key_id] = signature

                    await self.client.crypto.crypto_store.put_signature(
                        CrossSigner(s["user_id"], device.signing_key),
                        CrossSigner(self.client.mxid, self.client.crypto.account.signing_key),
                        signature
                    )

                    await self.client.upload_one_signature(s["user_id"], s["device_id"], full_keys)
                    logger.info(f"Signature for {s['device_id']} uploaded and saved locally.")
                except Exception as api_err:
                    logger.warning(f"Global signature upload failed: {api_err}")

            if s.get("result_future") and not s["result_future"].done():
                s["result_future"].set_result("success")

        except Exception as global_err:
            logger.error(f"Error in _maybe_finish: {global_err}")
            if s.get("result_future") and not s["result_future"].done():
                s["result_future"].set_result("error")
        finally:
            self.sessions.pop(txn_id, None)
