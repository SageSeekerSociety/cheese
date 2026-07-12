import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import InternalServerError


def _derive_key() -> bytes:
    """Derive a deterministic Fernet key from config when explicit key absent."""

    if settings.realname_encryption_key:
        try:
            # Validate provided key by attempting base64 decode.
            base64.urlsafe_b64decode(settings.realname_encryption_key)
            return settings.realname_encryption_key.encode("utf-8")
        except Exception as exc:  # pragma: no cover - invalid config
            raise InternalServerError(
                "Invalid REALNAME_ENCRYPTION_KEY configured"
            ) from exc

    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt_text(value: str) -> str:
    token = get_fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(token: str) -> str:
    try:
        plaintext = get_fernet().decrypt(token.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - indicates tampering
        raise InternalServerError("Failed to decrypt real-name field") from exc
