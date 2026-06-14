import os
from cryptography.fernet import Fernet

_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.getenv("ENCRYPTION_KEY", "").strip()
        if not key:
            raise RuntimeError("ENCRYPTION_KEY is not set in .env")
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(text: str) -> str:
    if not text:
        return text
    return _get_fernet().encrypt(text.encode()).decode()


def decrypt(text: str) -> str:
    if not text:
        return text
    try:
        return _get_fernet().decrypt(text.encode()).decode()
    except Exception:
        # Value is plain text (not yet encrypted) — return as-is for migration
        return text
