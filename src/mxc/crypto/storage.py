import contextlib
from typing import AsyncGenerator

from mautrix.client.state_store import MemoryStateStore as BaseMemoryStateStore
from mautrix.crypto.store import MemoryCryptoStore as BaseMemoryCryptoStore
from mautrix.types import CrossSigningUsage, TOFUSigningKey


class MemoryCryptoStore(BaseMemoryCryptoStore):
    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        yield

    async def put_cross_signing_key(self, user_id: str, usage: CrossSigningUsage, key: str) -> None:
        try:
            current = self._cross_signing_keys[user_id][usage]
            self._cross_signing_keys[user_id][usage] = TOFUSigningKey(key=key, first=current.first)
        except KeyError:
            self._cross_signing_keys.setdefault(user_id, {})[usage] = TOFUSigningKey(key=key, first=key)


class CustomMemoryStateStore(BaseMemoryStateStore):
    async def find_shared_rooms(self, user_id: str) -> list[str]:
        shared = []
        for room_id, members in getattr(self, "members", {}).items():
            if user_id in members:
                shared.append(room_id)
        return shared
