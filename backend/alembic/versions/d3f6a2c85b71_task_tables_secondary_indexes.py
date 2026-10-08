"""The 领取 and 提交 tables: the indexes their readers ask for

Revision ID: d3f6a2c85b71
Revises: 9e1b64d280dd
Create Date: 2026-10-07

``task_membership``, ``task_submission``, ``task_submission_entry`` and
``task_submission_review`` came in with ``a95752502bb0`` carrying a primary key
and nothing else: no ``__table_args__``, no ``Index()``, not one ``index=True``,
and 348 later migrations never added one (grepping them for ``create_index`` on
these four names finds nothing). Their foreign keys are all on the *referencing*
side — ``task_membership.task_id`` → ``task``, and so on — and PostgreSQL
auto-indexes only the side a key points *at*, so nothing was ever implicit.

Every one of these is read on a live path:

- `app.auth.domains.task` asks "did this person claim this 赛题" on every
  request, by ``task_id`` + ``member_id``.
- `visibility_service` runs the same correlated EXISTS once per candidate task
  and once per reader.
- `deadline_scheduler` sweeps every 900 s for ``deadline < now`` with nothing
  handed in.
- The submission tables are filtered by ``membership_id`` /
  ``task_submission_id`` / ``submission_id`` all over `app.domain.task.repositories`.

``team_user_relation`` is the same story one table over: it is the EXISTS every
team-scoped read runs — `app.auth.domains.team`, `app.auth.domains.knowledge`,
the team roster, and ``visibility_service``'s per-candidate-task EXISTS — and
all of them lead with ``team_id``. Its index is partial on ``deleted_at IS
NULL`` so a membership someone left does not sit in it.

Some readers filter ``user_id`` alone and are not served by an index that leads
with ``team_id``: ``UserStatisticsRepository.count_teams``,
``TeamRepository.list_teams_of_user`` and the two in
`app.domain.project.repositories` that collect a person's teams. Left alone on
purpose — each runs once for one person's own page, and an index on ``user_id``
would charge every membership change to speed up a page nobody loads in a loop.

The two partial predicates are spelled out here as the snapshot a migration
must be; the live copies are `app.domain.task.indexed_rows.LIVE_ROWS` and
``SWEEPABLE_ROWS``, and `tests/integration/test_task_indexes.py` fails when the
migration and the model's `Index()` stop agreeing. The sweep's own WHERE
(`deadline_scheduler`) reads the same literal text, because a partial index is
only used when the planner can prove the query implies its predicate, and a
bound ``IN ($1, $2)`` cannot imply a constant one — see that module's docstring
for the measurement.

Built CONCURRENTLY for the same reason as `c4d7e2a9f158`: 领取 and 提交 take
writes while the previous release is still serving. An invalid index left by a
failed concurrent build is dropped before the build is retried — otherwise
``if_not_exists=True`` would keep the broken one and never build a good one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f6a2c85b71"
# 接在 main 当下的链尾后面：本文件写下时那个头是 9f2b7c14a8e3，之后跟着 main 换过
# 两次——先 0c800ff1db2f，再是 blocks 那批索引（e5b1c7d29f04、9e1b64d280dd）之后的
# 9e1b64d280dd。main 每加一条迁移，这个值都要往后挪一次，同时改 alembic/HEAD。
down_revision: str | Sequence[str] | None = "9e1b64d280dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A 领取 that was not deleted. Every reader of `task_membership` and
# `team_user_relation` filters it, so it heads every partial index here (except
# the two on the submission tables, which have no such filter at their read
# points). `indexed_rows.LIVE_ROWS` is the live copy.
LIVE = "deleted_at IS NULL"

# Past its deadline with nothing in hand. `indexed_rows.SWEEPABLE_ROWS` is the
# live copy, and the sweep's WHERE is the same text.
SWEEPABLE = (
    "deleted_at IS NULL"
    " AND completion_status IN ('REJECTED_RESUBMITTABLE', 'NOT_SUBMITTED')"
)


def _drop_invalid(name: str, table: str) -> None:
    """Drop the index under this name if a concurrent build left it invalid.

    ``CREATE INDEX CONCURRENTLY`` that fails or is cancelled leaves a stub the
    planner ignores and ``if_not_exists=True`` would treat as done. Same guard
    as `c7e2a91f4d3b`. Must be called inside the autocommit block: the drop is
    CONCURRENTLY too.
    """
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )
    if invalid:
        op.drop_index(name, table_name=table, postgresql_concurrently=True)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _drop_invalid("ix_task_membership_task_member", "task_membership")
        op.create_index(
            "ix_task_membership_task_member",
            "task_membership",
            ["task_id", "member_id"],
            postgresql_where=sa.text(LIVE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid("ix_task_membership_member", "task_membership")
        op.create_index(
            "ix_task_membership_member",
            "task_membership",
            ["member_id"],
            postgresql_where=sa.text(LIVE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid("ix_task_membership_deadline", "task_membership")
        op.create_index(
            "ix_task_membership_deadline",
            "task_membership",
            ["deadline"],
            postgresql_where=sa.text(SWEEPABLE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid("ix_task_submission_membership_id", "task_submission")
        op.create_index(
            "ix_task_submission_membership_id",
            "task_submission",
            ["membership_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid("ix_task_submission_entry_submission_id", "task_submission_entry")
        op.create_index(
            "ix_task_submission_entry_submission_id",
            "task_submission_entry",
            ["task_submission_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid(
            "ix_task_submission_review_submission_id", "task_submission_review"
        )
        op.create_index(
            "ix_task_submission_review_submission_id",
            "task_submission_review",
            ["submission_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )

        _drop_invalid("ix_team_user_relation_team_user", "team_user_relation")
        op.create_index(
            "ix_team_user_relation_team_user",
            "team_user_relation",
            ["team_id", "user_id"],
            postgresql_where=sa.text(LIVE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_task_membership_task_member",
            table_name="task_membership",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_task_membership_member",
            table_name="task_membership",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_task_membership_deadline",
            table_name="task_membership",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_task_submission_membership_id",
            table_name="task_submission",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_task_submission_entry_submission_id",
            table_name="task_submission_entry",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_task_submission_review_submission_id",
            table_name="task_submission_review",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_team_user_relation_team_user",
            table_name="team_user_relation",
            postgresql_concurrently=True,
            if_exists=True,
        )
