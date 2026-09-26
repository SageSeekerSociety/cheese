"""Encryption for secrets the application stores at rest.

One master key (``DATA_ENCRYPTION_KEY``) roots everything. Each use derives
its own subkey from it with HKDF-SHA256, so a value can only be read back
under the purpose it was written for. Values are sealed with AES-256-GCM and
a random 96-bit nonce; the associated data names the record a value belongs
to (``bound_to``), so a ciphertext copied into another record's column fails
to decrypt instead of being read as that record's secret.

A stored value reads ``v1:<key id>:<base64url(nonce || ciphertext)>``. The key
id says which master key sealed it, which is what lets the key rotate: new
values use the first configured key, and any configured key opens old ones.
"""

import base64
import hashlib
import hmac
import os
from enum import StrEnum
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import (
    DEVELOPMENT_DATA_ENCRYPTION_KEY,
    parse_data_encryption_keys,
    settings,
)
from app.core.errors import InternalServerError

_VERSION = "v1"
_NONCE_BYTES = 12


class Purpose(StrEnum):
    """What a subkey is for. Each member is its own key: never reuse one."""

    TOTP_SECRET = "totp"
    TOTP_PENDING_SECRET = "totp-pending"
    TOTP_BACKUP_CODE = "totp-backup-code"
    FORGE_PASSWORD = "forge-password"
    FORGE_TOKEN = "forge-token"
    OAUTH_TOKEN = "oauth-token"
    LLM_SUBSCRIPTION_TOKEN = "llm-subscription-token"
    REALNAME = "realname"
    INTEGRATION_SECRET = "integration-secret"
    INTEGRATION_STATE = "integration-state"


class DecryptionError(InternalServerError):
    """A stored value that cannot be opened with the configured keys."""

    def __init__(self) -> None:
        super().__init__("Failed to decrypt a stored secret")


def key_id(master: bytes) -> str:
    """A short, non-secret name for a master key, recorded with its output."""
    return hashlib.sha256(b"cheese/key-id\x00" + master).hexdigest()[:8]


class Keyring:
    """The configured master keys, the first of which writes."""

    def __init__(self, masters: tuple[bytes, ...]) -> None:
        if not masters:
            raise ValueError("a keyring needs at least one key")
        self._masters = {key_id(master): master for master in masters}
        self.primary = key_id(masters[0])
        self._subkeys: dict[tuple[str, Purpose], bytes] = {}

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(self._masters)

    def subkey(self, kid: str, purpose: Purpose) -> bytes:
        cached = self._subkeys.get((kid, purpose))
        if cached is None:
            cached = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"cheese/" + purpose.value.encode(),
            ).derive(self._masters[kid])
            self._subkeys[(kid, purpose)] = cached
        return cached


@lru_cache(maxsize=4)
def _keyring_for(configured: str) -> Keyring:
    return Keyring(
        parse_data_encryption_keys(configured)
        or (base64.urlsafe_b64decode(DEVELOPMENT_DATA_ENCRYPTION_KEY),)
    )


def current_keyring() -> Keyring:
    return _keyring_for(settings.data_encryption_key)


def _associated_data(purpose: Purpose, bound_to: str) -> bytes:
    return b"cheese/" + purpose.value.encode() + b"\x00" + bound_to.encode()


def encrypt(
    purpose: Purpose,
    plaintext: str,
    *,
    bound_to: str,
    keyring: Keyring | None = None,
) -> str:
    """Seal ``plaintext`` for the record named by ``bound_to``."""
    ring = keyring or current_keyring()
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(ring.subkey(ring.primary, purpose)).encrypt(
        nonce, plaintext.encode(), _associated_data(purpose, bound_to)
    )
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    return f"{_VERSION}:{ring.primary}:{body}"


def decrypt(
    purpose: Purpose,
    stored: str,
    *,
    bound_to: str,
    keyring: Keyring | None = None,
) -> str:
    """Open a value written by ``encrypt`` for the same purpose and record."""
    ring = keyring or current_keyring()
    version, _, rest = stored.partition(":")
    kid, _, body = rest.partition(":")
    if version != _VERSION or kid not in ring.key_ids or not body:
        raise DecryptionError()
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        plaintext = AESGCM(ring.subkey(kid, purpose)).decrypt(
            raw[:_NONCE_BYTES],
            raw[_NONCE_BYTES:],
            _associated_data(purpose, bound_to),
        )
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError() from exc
    return plaintext.decode()


def keyed_digest(
    purpose: Purpose, data: bytes, *, keyring: Keyring | None = None
) -> tuple[str, bytes]:
    """HMAC-SHA256 of ``data`` under the writing key, with that key's id."""
    ring = keyring or current_keyring()
    return ring.primary, _hmac(ring, ring.primary, purpose, data)


def keyed_digests(
    purpose: Purpose, data: bytes, *, keyring: Keyring | None = None
) -> dict[str, bytes]:
    """``keyed_digest`` under every configured key, for matching old digests."""
    ring = keyring or current_keyring()
    return {kid: _hmac(ring, kid, purpose, data) for kid in ring.key_ids}


def _hmac(ring: Keyring, kid: str, purpose: Purpose, data: bytes) -> bytes:
    return hmac.new(ring.subkey(kid, purpose), data, hashlib.sha256).digest()
