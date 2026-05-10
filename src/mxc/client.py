import asyncio

from loguru import logger
from mautrix.client import Client, SyncStream
from mautrix.crypto import OlmMachine
from mautrix.crypto.store import CryptoStore
from mautrix.types import EventType, ToDeviceEvent

from mxc.crypto import BotSASVerification


class MXCClient(Client):
    def __init__(self, *args, crypto: bool = False, rate_limit_protect: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._crypto_enabled = crypto
        self.sas_verifier: BotSASVerification | None = None

        if rate_limit_protect:
            from mxc.utils import mautrix_rate_limit_patch
            mautrix_rate_limit_patch()

    async def init_crypto(
        self,
        crypto_store: CryptoStore,
        state_store,
    ):
        if not self._crypto_enabled:
            return

        self.state_store = state_store
        self.sync_store = crypto_store

        self.crypto = OlmMachine(self, crypto_store, state_store)
        self.crypto.allow_key_requests = True
        await self.crypto.load()

        self.sas_verifier = BotSASVerification(self)

        orig_decrypt = self.crypto._decrypt_olm_event

        async def hooked_decrypt(evt):
            dec = await orig_decrypt(evt)
            if dec and "m.key.verification" in (dec.type.t if hasattr(dec.type, "t") else str(dec.type)):
                asyncio.create_task(self.sas_verifier.handle_decrypted_event(dec))
            return dec

        self.crypto._decrypt_olm_event = hooked_decrypt

        from mautrix.client.encryption_manager import DecryptionDispatcher
        disp = self.dispatchers.pop(DecryptionDispatcher, None)
        if disp:
            disp.unregister()

        async def _catch_verification_evt(evt: ToDeviceEvent):
            type_str = evt.type.t if hasattr(evt.type, "t") else str(evt.type)
            if "m.key.verification" in type_str:
                await self.sas_verifier.handle_decrypted_event(evt)

        self.add_event_handler(
            EventType.ALL, _catch_verification_evt, sync_stream=SyncStream.TO_DEVICE,
        )

        if not await crypto_store.get_device_id():
            await crypto_store.put_device_id(self.device_id)
            await self.crypto.share_keys()
