"""device_token_digests —— a device's token is kept as its digest only

A machine connects with a durable token, and the database held that token as
it was: anyone who could read the `device` or `model_credential_hosts` table
could act as any machine. The backend now keeps the token's SHA-256 digest
(`app.domain.user.tokens.digest_token`, copied below as SQL) and hands the
token itself to the machine once.

This release adds the digests and stops reading the tokens. The still-serving
previous release goes on inserting rows with a token only, so a trigger fills
in their digest until the next release drops it together with the token
columns. Downgrading drops the digests; the tokens are still there.

Revision ID: a9c3e5f7b1d2
Revises: d7e1a2b3c4f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a9c3e5f7b1d2"
down_revision: str | Sequence[str] | None = "d7e1a2b3c4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("device", "model_credential_hosts")
# hashlib.sha256(token.encode()).hexdigest(), as `digest_token` writes it.
DIGEST = "encode(sha256(convert_to({}, 'UTF8')), 'hex')"


def upgrade() -> None:
    with_lock_retries("device, model_credential_hosts")
    op.execute(f"""
        CREATE FUNCTION device_token_digest_rollout() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.token IS NOT NULL THEN
                NEW.token_digest := {DIGEST.format("NEW.token")};
            END IF;
            RETURN NEW;
        END $$;
    """)
    for table in TABLES:
        op.add_column(table, sa.Column("token_digest", sa.String(64), nullable=True))
        op.alter_column(table, "token", existing_type=sa.String(128), nullable=True)
        op.execute(
            f"CREATE TRIGGER device_token_digest_rollout BEFORE INSERT OR UPDATE OF token"
            f" ON {table} FOR EACH ROW EXECUTE FUNCTION device_token_digest_rollout()"
        )
    with op.get_context().autocommit_block():
        for table in TABLES:
            # A handful of rows per enrolled machine.
            op.execute(
                f"UPDATE {table} SET token_digest = {DIGEST.format('token')}"
                " WHERE token_digest IS NULL AND token IS NOT NULL"
            )
            name = f"ix_{table}_token_digest"
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(
                name,
                table,
                ["token_digest"],
                unique=True,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with_lock_retries("device, model_credential_hosts")
    for table in TABLES:
        op.execute(f"DROP TRIGGER device_token_digest_rollout ON {table}")
        op.drop_index(f"ix_{table}_token_digest", table_name=table)
        op.drop_column(table, "token_digest")
    op.execute("DROP FUNCTION device_token_digest_rollout()")
