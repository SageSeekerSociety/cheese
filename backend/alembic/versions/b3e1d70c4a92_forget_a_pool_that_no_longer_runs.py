"""forget a pool that no longer runs

Revision ID: b3e1d70c4a92
Revises: d9b9ae09f3ec
"""

from alembic import op

revision = "b3e1d70c4a92"
down_revision = "d9b9ae09f3ec"
branch_labels = None
depends_on = None

# The per-turn SDK backends. `local-docker` was already gone from the catalog
# (#358) but kept running for rows that still named it; there is nothing behind
# either name now, so a row still holding one would show the room a pool that
# does not exist and send its turn to the fallback anyway.
GONE = ("local-docker", "remote-cheesed", "gpu")


def upgrade() -> None:
    # NULL, not the current default: a null means 「没选过」, and that is the
    # truth about these rows — the choice was made for them by a deployment
    # switch, never by anyone picking. Writing today's default instead would
    # freeze them onto it and outlive the next change of default.
    op.execute(
        f"UPDATE topics SET compute_profile = NULL "
        f"WHERE compute_profile IN {GONE}"  # noqa: S608 — literal tuple above
    )
    op.execute(
        f"UPDATE team SET compute_profile = NULL "
        f"WHERE compute_profile IN {GONE}"  # noqa: S608
    )
    # `settings` is json, not jsonb, and the key-delete operator is jsonb-only.
    op.execute(
        "UPDATE projects "
        "SET settings = (settings::jsonb - 'compute_profile')::json "
        f"WHERE settings ->> 'compute_profile' IN {GONE}"  # noqa: S608
    )


def downgrade() -> None:
    # Nothing to put back: the rows named backends this build cannot run.
    pass
