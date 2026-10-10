"""Encryption for secrets saved through the Settings API."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretBoxError(RuntimeError):
    pass


class SecretBox:
    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> bytes:
        if self._fernet is None:
            raise SecretBoxError("ORCHESTRA_SECRET_KEY is not configured; cannot store secrets")
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        if self._fernet is None:
            raise SecretBoxError("ORCHESTRA_SECRET_KEY is not configured; cannot read secrets")
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:  # key rotated or data corrupted
            raise SecretBoxError("Stored secret could not be decrypted") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
