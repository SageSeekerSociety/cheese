"""A machine's own skills, indexed — the body stays where the machine keeps it

Revision ID: b3f7a1c9d2e4
Revises: a1c9f4e07b62
Create Date: 2026-10-09

A skill is a directory holding a ``SKILL.md`` (agentskills.io), and the ones a
team wants are usually already on the machine: its owner wrote them for their
own agents (``~/.claude/skills``, ``~/.codex/skills``) or checked them in
(``.trae/skills``, ``.agents/skills``). The machine keeps the only copy. This
table records where that directory is (``source_root``) and what SKILL.md said
when it was last read (``digest``) — never the body. A body copied into the
database drifts from the file, and the copy is the one that goes stale.

``state`` starts at ``seen``: a scan finding a skill does not switch it on for
anyone, because a machine's home directory is not the platform's to switch on.
A person adopting it (``adopted``) is what makes it offerable.

``(device_id, name)`` is the primary key — the harnesses that collect skills
keep the first directory claiming a name — and its first column is the index the
foreign key needs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b3f7a1c9d2e4"
down_revision: str | Sequence[str] | None = "a1c9f4e07b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The new foreign key points at ``device``, which the live backend writes
    # to (a heartbeat) — the lock goes on the referenced table, not the new one.
    with_lock_retries("device")
    op.create_table(
        "machine_skill",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_root", sa.String(length=512), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="seen"),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            name="fk_machine_skill_device_id_device",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("device_id", "name", name="pk_machine_skill"),
    )


def downgrade() -> None:
    op.drop_table("machine_skill")
