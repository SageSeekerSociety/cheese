"""Keep 2FA secrets and backup codes in Postgres (#1482)

TOTP secrets, backup codes and the "always required" flag lived only in Redis,
outside every database backup; losing Redis switched everyone's second factor
off without a trace. This creates their tables and copies what Redis holds.

The copy runs here because every deploy runs ``alembic upgrade head`` inside
the backend container with the backend's env file, so REDIS_URL and
DATA_ENCRYPTION_KEY are both present, and it runs exactly once. A deployment
whose Redis cannot be reached stops here rather than lose its users' factors;
a development machine without Redis has nothing to copy and continues.

Setups still waiting for their confirming code are not copied: they expire
after ten minutes and the user starts again. The Redis keys themselves are left
in place; nothing reads them after this revision.

Revision ID: 2a88bca6e12e
Revises: e7033a179d9d
"""

import base64
import hashlib
import hmac
import logging
import os
from collections.abc import Sequence

import redis
import sqlalchemy as sa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic_settings import BaseSettings, SettingsConfigDict

from alembic import op

revision: str = "2a88bca6e12e"
down_revision: str | Sequence[str] | None = "e7033a179d9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_DEVELOPMENT_KEY = "Y2hlZXNlLWRldmVsb3BtZW50LWRhdGEta2V5LTAwMDA="


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    redis_url: str = "redis://localhost:6379/0"
    data_encryption_key: str = ""
    environment: str = "development"
    deployed_via_compose: bool = False


def _master(config: _Settings) -> bytes:
    entries = [e.strip() for e in config.data_encryption_key.split(",") if e.strip()]
    return base64.urlsafe_b64decode(entries[0] if entries else _DEVELOPMENT_KEY)


def _key_id(master: bytes) -> str:
    return hashlib.sha256(b"cheese/key-id\x00" + master).hexdigest()[:8]


def _subkey(master: bytes, purpose: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"cheese/" + purpose.encode(),
    ).derive(master)


def _seal_secret(master: bytes, user_id: int, secret: str) -> str:
    """``app.core.crypto.encrypt(Purpose.TOTP_SECRET, ..., bound_to=user)``."""
    nonce = os.urandom(12)
    associated = b"cheese/totp\x00" + f"user:{user_id}".encode()
    sealed = AESGCM(_subkey(master, "totp")).encrypt(nonce, secret.encode(), associated)
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    return f"v1:{_key_id(master)}:{body}"


def _backup_digest(master: bytes, user_id: int, sha256_hex: str) -> bytes:
    """The stored digest of a code Redis kept as SHA-256 of the code."""
    message = f"user:{user_id}:".encode() + bytes.fromhex(sha256_hex)
    return hmac.new(
        _subkey(master, "totp-backup-code"), message, hashlib.sha256
    ).digest()


def upgrade() -> None:
    op.create_table(
        "user_two_factor",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column(
            "always_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("pending_secret", sa.Text(), nullable=True),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_backup_code",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key_id", "digest", name="uq_user_backup_code"),
    )
    _copy_from_redis()


def _user_id(key: bytes, prefix: bytes) -> int | None:
    suffix = key[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


def _copy_from_redis() -> None:
    config = _Settings()
    client = redis.Redis.from_url(config.redis_url)
    try:
        _copy(config, client)
    finally:
        client.close()


def _copy(config: _Settings, client: redis.Redis) -> None:
    try:
        client.ping()
    except redis.exceptions.RedisError as exc:
        deployed = config.deployed_via_compose or config.environment not in (
            "development",
            "test",
        )
        if deployed:
            raise RuntimeError(
                "Cannot reach Redis to copy users' 2FA settings into Postgres; "
                "stopping so that no second factor is lost. Fix REDIS_URL or "
                "Redis and deploy again."
            ) from exc
        logger.warning("2FA copy skipped: Redis unreachable (%s)", exc)
        return

    bind = op.get_bind()
    master = _master(config)
    key_id = _key_id(master)

    def user_exists(user_id: int) -> bool:
        found = bind.execute(
            sa.text('SELECT 1 FROM "user" WHERE id = :id'), {"id": user_id}
        )
        return found.first() is not None

    copied = 0
    prefix = b"cheese:totp_secret:"
    for key in client.scan_iter(match=prefix + b"*"):
        user_id = _user_id(key, prefix)
        secret = client.get(key)
        if user_id is None or not secret or not user_exists(user_id):
            continue
        always = bool(client.exists(f"cheese:totp_always:{user_id}"))
        bind.execute(
            sa.text(
                "INSERT INTO user_two_factor (user_id, secret, always_required)"
                " VALUES (:user_id, :secret, :always)"
                # SCAN may return a key twice.
                " ON CONFLICT (user_id) DO NOTHING"
            ),
            {
                "user_id": user_id,
                "secret": _seal_secret(master, user_id, secret.decode()),
                "always": always,
            },
        )
        for digest in client.smembers(f"cheese:totp_backup:{user_id}"):
            bind.execute(
                sa.text(
                    "INSERT INTO user_backup_code (user_id, key_id, digest)"
                    " VALUES (:user_id, :key_id, :digest)"
                    " ON CONFLICT DO NOTHING"
                ),
                {
                    "user_id": user_id,
                    "key_id": key_id,
                    "digest": _backup_digest(master, user_id, digest.decode()),
                },
            )
        copied += 1

    prefix = b"cheese:totp_always:"
    for key in client.scan_iter(match=prefix + b"*"):
        user_id = _user_id(key, prefix)
        if user_id is None or not user_exists(user_id):
            continue
        bind.execute(
            sa.text(
                "INSERT INTO user_two_factor (user_id, always_required)"
                " VALUES (:user_id, true) ON CONFLICT (user_id) DO NOTHING"
            ),
            {"user_id": user_id},
        )
    logger.info("2FA copied from Redis for %d user(s)", copied)


def downgrade() -> None:
    enabled = op.get_bind().execute(
        sa.text("SELECT count(*) FROM user_two_factor WHERE secret IS NOT NULL")
    )
    if enabled.scalar_one():
        raise RuntimeError(
            "Users have 2FA enabled in Postgres, and the previous revision keeps "
            "it in Redis, where these secrets and backup codes cannot be "
            "written back. Refusing to drop them."
        )
    op.drop_table("user_backup_code")
    op.drop_table("user_two_factor")
