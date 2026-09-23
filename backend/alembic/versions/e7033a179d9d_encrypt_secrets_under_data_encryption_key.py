"""Re-encrypt stored secrets under DATA_ENCRYPTION_KEY (#1482)

Every secret column was a Fernet token under one key: REALNAME_ENCRYPTION_KEY
when set, otherwise a key derived from JWT_SECRET. The application now seals
them with AES-256-GCM under per-purpose subkeys of DATA_ENCRYPTION_KEY, bound to
the row they belong to (``app.core.crypto``). This rewrites each stored value
into that format.

The migration is self-contained on purpose: the old key derivation exists only
here, and the new format is written out rather than imported, so later changes
to ``app.core.crypto`` cannot change what this revision does.

Values already in the new format are left alone, so a rerun is harmless. A
value that neither format can open stops the migration and is named by table,
column and row, instead of being dropped.

Revision ID: e7033a179d9d
Revises: b672a09ef831
"""

import base64
import hashlib
import os
from collections.abc import Callable, Iterable, Sequence

import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from alembic import op

revision: str = "e7033a179d9d"
down_revision: str | Sequence[str] | None = "b672a09ef831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVELOPMENT_KEY = "Y2hlZXNlLWRldmVsb3BtZW50LWRhdGEta2V5LTAwMDA="


class _Keys(BaseSettings):
    """The settings both formats are keyed on, read the way the app reads them."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    jwt_secret: str = "dev-secret"
    realname_encryption_key: str = Field(default="", alias="REALNAME_ENCRYPTION_KEY")
    data_encryption_key: str = ""


def _fernet(keys: _Keys) -> Fernet:
    """The pre-#1482 key: REALNAME_ENCRYPTION_KEY, else derived from JWT_SECRET."""
    if keys.realname_encryption_key:
        return Fernet(keys.realname_encryption_key.encode("utf-8"))
    digest = hashlib.sha256(keys.jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _masters(keys: _Keys) -> list[bytes]:
    entries = [e.strip() for e in keys.data_encryption_key.split(",") if e.strip()]
    return [base64.urlsafe_b64decode(e) for e in entries or [_DEVELOPMENT_KEY]]


def _key_id(master: bytes) -> str:
    return hashlib.sha256(b"cheese/key-id\x00" + master).hexdigest()[:8]


def _aead(master: bytes, purpose: str) -> AESGCM:
    subkey = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"cheese/" + purpose.encode(),
    ).derive(master)
    return AESGCM(subkey)


def _associated(purpose: str, bound_to: str) -> bytes:
    return b"cheese/" + purpose.encode() + b"\x00" + bound_to.encode()


def _seal(master: bytes, purpose: str, bound_to: str, plaintext: str) -> str:
    nonce = os.urandom(12)
    sealed = _aead(master, purpose).encrypt(
        nonce, plaintext.encode(), _associated(purpose, bound_to)
    )
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    return f"v1:{_key_id(master)}:{body}"


def _open(masters: list[bytes], purpose: str, bound_to: str, stored: str) -> str:
    _, kid, body = stored.split(":", 2)
    master = next(m for m in masters if _key_id(m) == kid)
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    return (
        _aead(master, purpose)
        .decrypt(raw[:12], raw[12:], _associated(purpose, bound_to))
        .decode()
    )


# (table, key columns, [(column, purpose, bound_to(row))], extra filter)
_Column = tuple[str, str, Callable[[sa.Row], str]]
_TABLES: list[tuple[str, tuple[str, ...], list[_Column], str]] = [
    (
        "user_real_name_identities",
        ("id", "user_id"),
        [
            (column, "realname", lambda r, c=column: f"user:{r.user_id}:{c}")
            for column in ("real_name", "student_id", "grade", "major", "class_name")
        ],
        "encrypted",
    ),
    (
        "project_forges",
        ("id", "project_id"),
        [
            (
                "account_password",
                "forge-password",
                lambda r: f"project:{r.project_id}",
            )
        ],
        "true",
    ),
    (
        "forge_tokens",
        ("id", "project_id", "api_url", "username"),
        [
            (
                "value",
                "forge-token",
                lambda r: f"project:{r.project_id}:{r.api_url}:{r.username}",
            )
        ],
        "true",
    ),
    (
        "user_o_auth_connection",
        ("id", "user_id", "provider_id"),
        [
            (
                column,
                "oauth-token",
                lambda r, c=column: f"user:{r.user_id}:{r.provider_id}:{c}",
            )
            for column in ("access_token", "refresh_token")
        ],
        "true",
    ),
    (
        "llm_subscriptions",
        ("id",),
        [
            (
                column,
                "llm-subscription-token",
                lambda r, c=column: f"llm-subscription:{r.id}:{c}",
            )
            for column in ("access_token_enc", "refresh_token_enc", "id_token_enc")
        ],
        "true",
    ),
]


def _rewrite(convert: Callable[[str, str, str], str | None]) -> None:
    """Pass every stored secret through ``convert(purpose, bound_to, value)``.

    ``convert`` returns the value to store, or None to leave the row alone.
    Every value it cannot convert is collected and reported together.
    """
    bind = op.get_bind()
    failures: list[str] = []
    for table, keys, columns, where in _TABLES:
        names = [*keys, *(column for column, _, _ in columns)]
        rows: Iterable[sa.Row] = bind.execute(
            sa.text(f"SELECT {', '.join(names)} FROM {table} WHERE {where}")
        )
        for row in list(rows):
            updates: dict[str, str] = {}
            for column, purpose, bound_to in columns:
                value = getattr(row, column)
                if not value:
                    continue
                try:
                    converted = convert(purpose, bound_to(row), value)
                except (InvalidToken, InvalidTag, StopIteration, ValueError):
                    converted = None
                    failures.append(f"{table}.{column} id={row.id}")
                if converted is not None:
                    updates[column] = converted
            if updates:
                assignments = ", ".join(f"{c} = :{c}" for c in updates)
                bind.execute(
                    sa.text(f"UPDATE {table} SET {assignments} WHERE id = :id"),
                    {**updates, "id": row.id},
                )
    if failures:
        raise RuntimeError(
            "These stored secrets cannot be decrypted with the configured keys, "
            "so the migration stopped rather than lose them. Restore the key "
            "they were written with, or clear the listed values by hand if they "
            "are known to be unusable: " + "; ".join(failures)
        )


def upgrade() -> None:
    keys = _Keys()
    old = _fernet(keys)
    master = _masters(keys)[0]

    def convert(purpose: str, bound_to: str, value: str) -> str | None:
        if value.startswith("v1:"):
            return None
        return _seal(master, purpose, bound_to, old.decrypt(value.encode()).decode())

    _rewrite(convert)


def downgrade() -> None:
    keys = _Keys()
    old = _fernet(keys)
    masters = _masters(keys)

    def convert(purpose: str, bound_to: str, value: str) -> str | None:
        if not value.startswith("v1:"):
            return None
        plaintext = _open(masters, purpose, bound_to, value)
        return old.encrypt(plaintext.encode()).decode()

    _rewrite(convert)
