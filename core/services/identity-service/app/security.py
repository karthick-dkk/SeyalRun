from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings

_ph = PasswordHasher()
_PAT_PREFIX = "sr_"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def generate_pat() -> str:
    return _PAT_PREFIX + secrets.token_urlsafe(32)


def pat_prefix(raw_token: str) -> str:
    return raw_token[: len(_PAT_PREFIX) + 6]


def hash_pat(raw_token: str) -> str:
    settings = get_settings()
    return _ph.hash(raw_token + settings.api_token_pepper)


def verify_pat(raw_token: str, token_hash: str) -> bool:
    settings = get_settings()
    try:
        return _ph.verify(token_hash, raw_token + settings.api_token_pepper)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
