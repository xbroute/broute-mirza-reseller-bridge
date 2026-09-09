from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def from_file(cls, path: Path) -> "SecretBox":
        key = path.read_bytes().strip()
        return cls(key)

    @staticmethod
    def ensure_key_file(path: Path) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, Fernet.generate_key() + b"\n")
        finally:
            os.close(fd)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt stored credential") from exc
