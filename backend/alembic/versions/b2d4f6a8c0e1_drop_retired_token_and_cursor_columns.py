"""drop_retired_token_and_cursor_columns —— the columns two deployed releases stopped reading

`a9c3e5f7b1d2` (#4865) keeps only a digest of a machine's durable token and
unmapped `device.token` and `model_credential_hosts.token`, with a trigger
filling the digest for rows the release before it inserted. That release is
deployed and no backend still writes a token, so the trigger, its function and
the plaintext tokens go. `assistant_routines.event_cursor` was unmapped by
#4827 when happenings replaced polling the notification stream.

Dropping a column takes its index with it (`ix_device_token`,
`ix_model_credential_hosts_token`). The tokens cannot be brought back:
downgrading refuses.

Revision ID: b2d4f6a8c0e1
Revises: a9c3e5f7b1d2
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: str | Sequence[str] | None = "a9c3e5f7b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("device, model_credential_hosts, assistant_routines")
    op.execute("DROP TRIGGER IF EXISTS device_token_digest_rollout ON device")
    op.execute(
        "DROP TRIGGER IF EXISTS device_token_digest_rollout ON model_credential_hosts"
    )
    op.execute("DROP FUNCTION IF EXISTS device_token_digest_rollout()")
    op.drop_column("device", "token")
    op.drop_column("model_credential_hosts", "token")
    op.drop_column("assistant_routines", "event_cursor")


def downgrade() -> None:
    raise RuntimeError("Dropped device tokens cannot be reconstructed")
