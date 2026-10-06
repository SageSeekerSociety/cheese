"""A compaction whose turn has ended says it did not finish

Revision ID: f4378cf0084d
Revises: c9a4f2e15b7d
Create Date: 2026-10-06

A compaction's record says "compacting" until the hook stream restates it as
over. Until 851836023 (#2881) the record to restate was found only in the
memory of the backend that wrote it, so a compaction under way when dev
replaced its backend went on saying "compacting" after its turn had ended, and
nothing would ever restate it. Since then a turn's Stop closes every compaction
record of that turn still running, whichever backend wrote it; this closes the
ones left from before.

Which records: `context_compact`, still `running`, of a turn that is not
running. Running is exactly an `agent_turns` row with `stopped_at IS NULL`, so
a turn whose row is stopped has ended, and so has one with no row at all: no
backend and no sweep counts it as running. A record of a running turn is left
to that turn's own end.

Each is restated as the hook stream restates one when a turn stops with its
compaction open: "上下文整理没有完成", reason "会话在整理完成前结束了", as of the
moment its turn stopped (now, for a turn with no row). The sentences are
written out rather than imported: a migration keeps saying what it said when
it ran, whatever the catalogue says later.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f4378cf0084d"
down_revision: str | Sequence[str] | None = "c9a4f2e15b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    close()


def close() -> None:
    """Restate every compaction record of an ended turn as over."""
    op.execute("""
        UPDATE run_records r
        SET content = '上下文整理没有完成',
            severity = 'warn',
            meta = (
                COALESCE(r.meta::jsonb, '{}'::jsonb)
                || jsonb_build_object(
                    'state', 'over',
                    'severity', 'warn',
                    'detail', '会话在整理完成前结束了',
                    'detail_label', '原因',
                    'at', to_char(
                        ended.at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                    ),
                    'i18n', COALESCE(r.meta::jsonb -> 'i18n', '{}'::jsonb)
                        || jsonb_build_object(
                            'content', jsonb_build_object(
                                'key', 'contextCompactFailed',
                                'params', '{}'::jsonb),
                            'detail', jsonb_build_object(
                                'key', 'contextCompactSessionEnded',
                                'params', '{}'::jsonb),
                            'detail_label', jsonb_build_object(
                                'key', 'labelReason',
                                'params', '{}'::jsonb)
                        )
                )
            )::json,
            updated_at = now()
        FROM (
            SELECT c.id, COALESCE(t.stopped_at, now()) AS at
            FROM run_records c
            LEFT JOIN agent_turns t ON t.id = c.turn_id
            WHERE c.kind = 'context_compact'
              AND c.meta ->> 'state' = 'running'
              AND c.turn_id IS NOT NULL
              AND (t.id IS NULL OR t.stopped_at IS NOT NULL)
        ) ended
        WHERE r.id = ended.id
    """)


def downgrade() -> None:
    # Which records said "compacting" before is not kept, and saying it again
    # of a turn that has ended would be untrue.
    pass
