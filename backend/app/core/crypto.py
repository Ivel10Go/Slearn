import base64
import binascii
import json
import os
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_KEY_LENGTH: Final = 32
_NONCE_LENGTH: Final = 12
_PAYLOAD_PREFIX: Final = "sa1."
_ADDITIONAL_DATA: Final = b"school-assistant:integration-credentials:v1"
_KEY_INFO: Final = b"school-assistant:integration-encryption-key"


class CredentialDecryptionError(RuntimeError):
    pass


def _derive_key(master_secret: str) -> bytes:
    if not master_secret.strip():
        raise ValueError("Integration encryption key must not be empty")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=None,
        info=_KEY_INFO,
    ).derive(master_secret.encode("utf-8"))


def encrypt_credentials(credentials: dict[str, Any], master_secret: str) -> str:
    if not isinstance(credentials, dict):
        raise TypeError("Credentials must be a mapping")
    if not credentials:
        raise ValueError("Credentials must not be empty")
    normalized = {str(key): "" if value is None else str(value) for key, value in credentials.items()}
    if any(not key for key in normalized):
        raise ValueError("Credential keys must not be empty")
    nonce = os.urandom(_NONCE_LENGTH)
    plaintext = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(_derive_key(master_secret)).encrypt(nonce, plaintext, _ADDITIONAL_DATA)
    return _PAYLOAD_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_credentials(payload: str, master_secret: str) -> dict[str, str]:
    if not payload.startswith(_PAYLOAD_PREFIX):
        raise CredentialDecryptionError("Stored credentials use an unsupported format")
    encoded = payload[len(_PAYLOAD_PREFIX) :]
    if not encoded:
        raise CredentialDecryptionError("Stored credentials are empty")
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise CredentialDecryptionError("Stored credentials are not valid base64") from exc
    nonce, ciphertext = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    if len(nonce) != _NONCE_LENGTH or not ciphertext:
        raise CredentialDecryptionError("Stored credentials are truncated")
    try:
        plaintext = AESGCM(_derive_key(master_secret)).decrypt(nonce, ciphertext, _ADDITIONAL_DATA)
    except InvalidTag as exc:
        raise CredentialDecryptionError(
            "Stored credentials could not be decrypted with the configured key"
        ) from exc
    try:
        decoded = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialDecryptionError("Stored credentials are not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise CredentialDecryptionError("Stored credentials have an unexpected structure")
    return {str(key): "" if value is None else str(value) for key, value in decoded.items()}


def has_credentials(payload: str | None) -> bool:
    return bool(payload)
