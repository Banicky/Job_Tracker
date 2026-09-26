import os

from cryptography.fernet import Fernet

_fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"])


def encrypt(value: str | None) -> str | None:
    return _fernet.encrypt(value.encode()).decode() if value else None


def decrypt(value: str | None) -> str | None:
    return _fernet.decrypt(value.encode()).decode() if value else None
