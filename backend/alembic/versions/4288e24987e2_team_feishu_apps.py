"""飞书应用设在团队上：每个飞书连接用它所在团队的应用

Revision ID: 4288e24987e2
Revises: fbdb95e20845
Create Date: 2026-11-24

企业自建应用只在它那家企业的飞书里能用，团队是持有它的那一层：公司的团队设公司的应用，
一个人自己用就设在他的个人团队上。``team_feishu_apps`` 每个团队至多一行（主键就是
``team_id``），App Secret 密封、绑在团队上。成员的连接（``integrations`` 里
``provider='feishu'`` 的行）只记 ``config.team_id`` 和本人的 token。

**数据迁移**：原来自带凭据的连接（``config.app_id`` + 密封在 ``secret`` 里的
``app_secret``）挪到主人的个人团队上：没有个人团队就建一个（和
``TeamService.ensure_personal_team`` 建的一样），个人团队还没有应用就用这一行的凭据建；
连接改成指向这个团队，``secret`` 里去掉 ``app_secret``、留下 token。一个人有几行、
各带不同的应用时，最近更新的那一行的应用成为个人团队的应用，其余几行也指向它：它们的
token 是别的应用发的，下一次刷新被飞书拒，连接标成授权失效，主人重新连接即可。

密钥的格式照 ``app.core.crypto`` 写在这里而不是引用它，和
``e7033a179d9d`` 一样：迁移不能随应用代码的改动而变。
"""

import base64
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic_settings import BaseSettings, SettingsConfigDict

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4288e24987e2"
down_revision: str | Sequence[str] | None = "fbdb95e20845"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVELOPMENT_KEY = "Y2hlZXNlLWRldmVsb3BtZW50LWRhdGEta2V5LTAwMDA="
_PURPOSE = "integration-secret"
_OWNER = 0


class _Keys(BaseSettings):
    """The key setting, read the way the app reads it."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    data_encryption_key: str = ""


def _masters() -> list[bytes]:
    keys = _Keys()
    entries = [e.strip() for e in keys.data_encryption_key.split(",") if e.strip()]
    return [base64.urlsafe_b64decode(e) for e in entries or [_DEVELOPMENT_KEY]]


def _key_id(master: bytes) -> str:
    return hashlib.sha256(b"cheese/key-id\x00" + master).hexdigest()[:8]


def _aead(master: bytes) -> AESGCM:
    subkey = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"cheese/" + _PURPOSE.encode(),
    ).derive(master)
    return AESGCM(subkey)


def _associated(bound_to: str) -> bytes:
    return b"cheese/" + _PURPOSE.encode() + b"\x00" + bound_to.encode()


def _seal(masters: list[bytes], bound_to: str, value: dict) -> str:
    master = masters[0]
    nonce = os.urandom(12)
    sealed = _aead(master).encrypt(
        nonce, json.dumps(value).encode(), _associated(bound_to)
    )
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    return f"v1:{_key_id(master)}:{body}"


def _open(masters: list[bytes], bound_to: str, stored: str) -> dict:
    _, kid, body = stored.split(":", 2)
    master = next(m for m in masters if _key_id(m) == kid)
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    plain = _aead(master).decrypt(raw[:12], raw[12:], _associated(bound_to))
    return json.loads(plain.decode())


def _team_binding(team_id: int) -> str:
    return f"team-feishu-app:{team_id}"


def _personal_team(conn: sa.Connection, user_id: int) -> int:
    found = conn.execute(
        sa.text(
            "SELECT id FROM team WHERE personal_owner_user_id = :u"
            " AND deleted_at IS NULL"
        ),
        {"u": user_id},
    ).scalar()
    if found is not None:
        return int(found)
    now = datetime.now(UTC)
    team_id = conn.execute(
        sa.text(
            "INSERT INTO team (id, name, intro, description, avatar_id,"
            " personal_owner_user_id, created_at, updated_at)"
            " VALUES (nextval('team_seq'), '个人', '', '', 0, :u, :now, :now)"
            " RETURNING id"
        ),
        {"u": user_id, "now": now},
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO team_user_relation (id, team_id, user_id, role,"
            " created_at, updated_at)"
            " VALUES (nextval('team_user_relation_seq'), :t, :u, :r, :now, :now)"
        ),
        {"t": team_id, "u": user_id, "r": _OWNER, "now": now},
    )
    return int(team_id)


def upgrade() -> None:
    op.create_table(
        "team_feishu_apps",
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_team_feishu_apps_team_id_team",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id"),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, owner_user_id, owner_handle, config, secret FROM integrations"
            " WHERE provider = 'feishu' AND coalesce(config->>'app_id', '') <> ''"
            " ORDER BY updated_at DESC"
        )
    ).all()
    if not rows:
        return
    masters = _masters()
    now = datetime.now(UTC)
    for row in rows:
        secret = _open(masters, str(row.id), row.secret)
        team_id = _personal_team(conn, row.owner_user_id)
        has_app = conn.execute(
            sa.text("SELECT 1 FROM team_feishu_apps WHERE team_id = :t"),
            {"t": team_id},
        ).scalar()
        if not has_app:
            conn.execute(
                sa.text(
                    "INSERT INTO team_feishu_apps (team_id, app_id, domain, secret,"
                    " updated_by, created_at, updated_at)"
                    " VALUES (:t, :a, :d, :s, :by, :now, :now)"
                ),
                {
                    "t": team_id,
                    "a": row.config["app_id"],
                    "d": row.config.get("domain") or "feishu",
                    "s": _seal(
                        masters,
                        _team_binding(team_id),
                        {"app_secret": secret.get("app_secret") or ""},
                    ),
                    "by": row.owner_handle,
                    "now": now,
                },
            )
        config = {k: v for k, v in row.config.items() if k not in ("app_id", "domain")}
        config["team_id"] = team_id
        kept = {k: v for k, v in secret.items() if k != "app_secret"}
        conn.execute(
            sa.text(
                "UPDATE integrations SET config = CAST(:c AS jsonb), secret = :s"
                " WHERE id = :id"
            ),
            {
                "c": json.dumps(config),
                "s": _seal(masters, str(row.id), kept),
                "id": row.id,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    masters = _masters()
    rows = conn.execute(
        sa.text(
            "SELECT i.id, i.config, i.secret, a.team_id, a.app_id, a.domain,"
            " a.secret AS app_secret"
            " FROM integrations i JOIN team_feishu_apps a"
            " ON a.team_id = CAST(i.config->>'team_id' AS bigint)"
            " WHERE i.provider = 'feishu'"
        )
    ).all()
    for row in rows:
        app = _open(masters, _team_binding(row.team_id), row.app_secret)
        secret = _open(masters, str(row.id), row.secret)
        config = {k: v for k, v in row.config.items() if k != "team_id"}
        config.update(app_id=row.app_id, domain=row.domain)
        conn.execute(
            sa.text(
                "UPDATE integrations SET config = CAST(:c AS jsonb), secret = :s"
                " WHERE id = :id"
            ),
            {
                "c": json.dumps(config),
                "s": _seal(
                    masters,
                    str(row.id),
                    {**secret, "app_secret": app.get("app_secret") or ""},
                ),
                "id": row.id,
            },
        )
    op.drop_table("team_feishu_apps")
