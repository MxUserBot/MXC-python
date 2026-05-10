import json
from threading import RLock
from rocksdict import Rdict
from loguru import logger
from cryptography.fernet import Fernet


class Database:
    def __init__(self, master_key: bytes, path: str = "mxu.rocksdb"):
        import os

        self.db = Rdict(path)
        self._lock = RLock()

        self._set_permissions(path)

        self._cipher = Fernet(master_key)

    @staticmethod
    def _set_permissions(path: str):
        import os

        os.chmod(path, 0o700)
        for root, dirs, files in os.walk(path):
            for f in files:
                os.chmod(os.path.join(root, f), 0o600)

    async def get(self, owner: str, key: str, default=None):
        db_key = self._make_key(owner, key)
        try:
            raw = self._raw_get(db_key)
            if raw is not None:
                return json.loads(self._cipher.decrypt(raw.decode()).decode())
            return default
        except Exception as e:
            logger.error(f"Read error: {e}")
            return default

    async def set(self, owner: str, key: str, value: any):
        db_key = self._make_key(owner, key)
        encrypted = self._cipher.encrypt(json.dumps(value).encode())
        try:
            self._raw_set(db_key, encrypted)
            return True
        except Exception as e:
            logger.error(f"Write error: {e}")
            return False

    async def delete(self, owner: str, key: str):
        db_key = self._make_key(owner, key)
        self._raw_delete(db_key)

    async def flush(self):
        with self._lock:
            self.db.flush()

    def close(self):
        with self._lock:
            self.db.close()

    def _make_key(self, owner: str, key: str) -> str:
        return f"{owner}:{key}"

    def _raw_get(self, db_key: str):
        with self._lock:
            return self.db.get(db_key)

    def _raw_set(self, db_key: str, value: bytes):
        with self._lock:
            self.db[db_key] = value

    def _raw_delete(self, db_key: str):
        with self._lock:
            self.db.delete(db_key)

    async def items_with_prefix(self, owner: str, prefix: str):
        db_prefix = f"{owner}:{prefix}"
        items = []
        with self._lock:
            for key, value in self.db.items(from_key=db_prefix):
                if not key.startswith(db_prefix):
                    break
                suffix = key[len(owner) + 1:]
                try:
                    items.append((suffix, json.loads(self._cipher.decrypt(value.decode()).decode())))
                except Exception:
                    continue
        return items
