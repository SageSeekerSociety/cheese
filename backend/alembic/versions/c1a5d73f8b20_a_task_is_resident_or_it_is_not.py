"""a task is resident, or it is not

一个房间最多同时开 4 条后台子代理。The cap has to be counted against something,
and the obvious candidate — "how many open tasks does this room have" — is the
wrong one: a task that finished its turn and is waiting for somebody to read it
is not using anything, and four of those would wedge a room forever with nobody
able to see why.

So the cap counts RESIDENCY, which is what DeepSeek Harness counts too:

    running  — a turn is going, or one is queued to start
    idle     — quiet; the slot is released

(dsh has a third, `waiting` — quiet but still owning unfinished children. 活不
嵌套 here, so it would be a value nothing ever writes.)

`idle` is not "finished". A task can be woken back up — someone replies in its
thread, the room tells it something — and it becomes `running` again with its
conversation and its files exactly where they were. Releasing the slot is
therefore free: nothing is lost by it, so nothing has to be decided by a human
before it happens.

That is the whole reason `tasks.status` (open/closed) is left alone. It answers
"is this work still wanted", which is a person's judgement; residency answers
"is this work using a slot right now", which is observable. Folding them
together is exactly how the deadlock this migration avoids gets built.

## queued_at

A room at its cap does not refuse new work, it defers it — a refusal makes the
dispatcher decide what to do about a condition that will clear on its own in a
few minutes. A task with `queued_at` set has been dispatched and is waiting for
a slot; it has a thread, a brief and an owner already, so it is visible as
"queued" rather than missing.

Revision ID: c1a5d73f8b20
Revises: b8e2f4a90d33
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a5d73f8b20"
down_revision: str | Sequence[str] | None = "b8e2f4a90d33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "residency", sa.String(length=16), nullable=False, server_default="idle"
        ),
    )
    op.add_column(
        "tasks", sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Counting a room's resident tasks is done on every dispatch and on every
    # turn boundary; it must not become a scan as a long-lived room accumulates
    # hundreds of finished threads.
    op.create_index("ix_tasks_room_id_residency", "tasks", ["room_id", "residency"])
    # The queue is read newest-slot-first: which task starts when one frees up.
    op.create_index(
        "ix_tasks_room_id_queued_at",
        "tasks",
        ["room_id", "queued_at"],
        postgresql_where=sa.text("queued_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_room_id_queued_at", table_name="tasks")
    op.drop_index("ix_tasks_room_id_residency", table_name="tasks")
    op.drop_column("tasks", "queued_at")
    op.drop_column("tasks", "last_turn_at")
    op.drop_column("tasks", "residency")
