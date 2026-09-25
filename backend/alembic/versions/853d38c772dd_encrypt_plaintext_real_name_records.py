"""Encrypt the real-name records still stored as plaintext

Records written before real-name fields were encrypted carry
``encrypted = false`` and hold their text as written. e7033a179d9d moved the
encrypted ones onto DATA_ENCRYPTION_KEY but selected only ``WHERE encrypted``,
so these were never touched. This seals every column of each such row the way
the application does (``seal_realname_field``: purpose ``realname``, bound to
``user:<user_id>:<column>``) and marks the row encrypted.

Self-contained for the same reason as e7033a179d9d: the format is written out
here rather than imported, so later changes to ``app.core.crypto`` cannot
change what this revision does. The key is read the way the app reads it;
``alembic/env.py`` loads the app settings first, which refuse to start a
deployment without a real DATA_ENCRYPTION_KEY.

Only plaintext rows are selected, so a rerun changes nothing. Empty values
stay empty: the app reads an empty column as empty whether or not the row is
encrypted.

Downgrade leaves the rows encrypted. The previous revision reads encrypted
rows already, and writing personal data back out as plaintext is not a
rollback anyone needs.

Revision ID: 853d38c772dd
Revises: 90e599b0ba34
"""

import base64
import hashlib
import os
from collections.abc import Sequence

import sqlalchemy as sa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic_settings import BaseSettings, SettingsConfigDict

from alembic import op

revision: str = "853d38c772dd"
down_revision: str | Sequence[str] | None = "90e599b0ba34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVELOPMENT_KEY = "Y2hlZXNlLWRldmVsb3BtZW50LWRhdGEta2V5LTAwMDA="
_COLUMNS = ("real_name", "student_id", "grade", "major", "class_name")


class _Keys(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    data_encryption_key: str = ""


def _writing_key() -> bytes:
    """The first configured key, which is the one the app writes with."""
    configured = _Keys().data_encryption_key
    entries = [e.strip() for e in configured.split(",") if e.strip()]
    return base64.urlsafe_b64decode(entries[0] if entries else _DEVELOPMENT_KEY)


def _seal(master: bytes, bound_to: str, plaintext: str) -> str:
    subkey = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"cheese/realname"
    ).derive(master)
    nonce = os.urandom(12)
    sealed = AESGCM(subkey).encrypt(
        nonce, plaintext.encode(), b"cheese/realname\x00" + bound_to.encode()
    )
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    key_id = hashlib.sha256(b"cheese/key-id\x00" + master).hexdigest()[:8]
    return f"v1:{key_id}:{body}"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT id, user_id, {', '.join(_COLUMNS)} "
            "FROM user_real_name_identities WHERE NOT encrypted"
        )
    ).all()
    if not rows:
        return
    master = _writing_key()
    for row in rows:
        values = {
            column: _seal(master, f"user:{row.user_id}:{column}", value)
            if (value := getattr(row, column))
            else value
            for column in _COLUMNS
        }
        assignments = ", ".join(f"{column} = :{column}" for column in _COLUMNS)
        bind.execute(
            sa.text(
                f"UPDATE user_real_name_identities SET {assignments}, "
                "encrypted = true WHERE id = :id AND NOT encrypted"
            ),
            {**values, "id": row.id},
        )


def downgrade() -> None:
    pass
