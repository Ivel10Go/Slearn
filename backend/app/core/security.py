import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from pydantic import SecretStr

_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: int
    issued_at: int
    expires_at: int


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _secret_value(secret: SecretStr | str) -> str:
    value = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    if not value:
        raise ValueError("Secret must not be empty")
    return value


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_segment(value: str) -> bytes:
    if not value:
        raise ValueError("Empty token segment")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Invalid token encoding") from exc


def create_session_token(user_id: int, secret: SecretStr | str, max_age_seconds: int) -> str:
    if user_id <= 0:
        raise ValueError("User id must be positive")
    if max_age_seconds <= 0:
        raise ValueError("Session lifetime must be positive")
    secret_value = _secret_value(secret)
    issued_at = int(time.time())
    expires_at = issued_at + max_age_seconds
    payload = {"sub": user_id, "iat": issued_at, "exp": expires_at, "v": 1}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_segment = _encode_segment(payload_bytes)
    signature = hmac.new(secret_value.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_segment}.{_encode_segment(signature)}"


def decode_session_token(token: str, secret: SecretStr | str) -> TokenClaims | None:
    try:
        secret_value = _secret_value(secret)
        payload_segment, signature_segment = token.split(".", maxsplit=1)
        payload_bytes = _decode_segment(payload_segment)
        signature = _decode_segment(signature_segment)
        expected = hmac.new(secret_value.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload: Any = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        user_id = payload["sub"]
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if isinstance(user_id, bool) or isinstance(issued_at, bool) or isinstance(expires_at, bool):
            return None
        user_id = int(user_id)
        issued_at = int(issued_at)
        expires_at = int(expires_at)
        now = int(time.time())
        if payload.get("v") != 1 or user_id <= 0 or expires_at <= now or issued_at > expires_at or issued_at > now + 30:
            return None
        return TokenClaims(user_id=user_id, issued_at=issued_at, expires_at=expires_at)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError, OverflowError):
        return None
