"""Run records leave the conversation

Revision ID: b672fdeb358e
Revises: 53267de872b8
Create Date: 2026-10-06

What the platform did while running a conversation — a turn queued, a sandbox
woken or put to sleep, a model request retried, memory written, a timed
delivery handed over — and the platform's own errors were lines in `blocks`,
said in the conversation. They move to `run_records`, which the 现场 and the
admin page read and the conversation does not.

Rows older than the records' retention (30 days) are not moved, only deleted.
The queue notices of the 2026-10-05/06 storm (a turn that could not start was
re-dispatched every few seconds, and each attempt said so) carry nothing a
reader needs and are deleted rather than moved. Two kinds of row stay where
they are: one a 支线 hangs under, and one still carrying a sentence for an
agent (`agent_notice`), which only `blocks` delivers.

`ix_blocks_cloud_provisioning` served a read nothing uses any more, and the
machine-event index narrows to the one machine event still said in the
conversation.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b672fdeb358e"
down_revision: str | Sequence[str] | None = "53267de872b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MOVED = (
    "turn_queued",
    "delivery_checking",
    "delivery_fallback",
    "tools_recovered",
    "prompt_replayed",
    "api_retry",
    "context_compact",
    "device_waiting",
    "timed_delivery",
    "cloud_startup",
    "cloud_provisioning",
    "sandbox_asleep",
    "memory_changed",
    "backend_error",
    "frontend_error",
)
#: Errors belong to no conversation: the one they happened in is kept in meta.
UNATTACHED = ("backend_error", "frontend_error")
#: Lines that said the same as a typed one but carried no type.
UNTYPED = {
    "输入已登记，发送结果正在核对；不会重复发送": "delivery_checking",
    "机器上的会话正在启动，消息已就位，会自动发送": "turn_queued",
}
STORM = ("2026-10-05 14:00:00+00", "2026-10-06 06:30:00+00")
#: Not handed to the run, just the agent seats the platform itself writes as.
NOT_A_SEAT = ("system", "backend", "frontend", "accept")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for every table, a few seconds at a time."""
    op.execute(f"""
        DO $$
        DECLARE
            attempts integer := 0;
            outer_timeout text := current_setting('lock_timeout');
        BEGIN
            PERFORM set_config('lock_timeout', '3s', true);
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE;
                    EXIT;
                EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
                    attempts := attempts + 1;
                    IF attempts >= 100 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.2);
                END;
            END LOOP;
            PERFORM set_config('lock_timeout', outer_timeout, true);
        END
        $$
    """)


def upgrade() -> None:
    op.create_table(
        "run_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("seat", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_records_project_id", "run_records", ["project_id"])
    op.create_index(
        "ix_run_records_conversation_created",
        "run_records",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_run_records_turn", "run_records", ["turn_id"])
    op.create_index(
        "ix_run_records_kind_created", "run_records", ["kind", "created_at"]
    )
    op.create_index("ix_run_records_created", "run_records", ["created_at"])

    _lock("blocks")
    move()

    op.drop_index("ix_blocks_cloud_provisioning", table_name="blocks")
    op.drop_index("ix_blocks_machine_events", table_name="blocks")
    op.create_index(
        "ix_blocks_machine_events",
        "blocks",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text("(meta ->> 'event_type') IN ('environment_repaired')"),
    )


def move() -> None:
    """Move the platform's running out of `blocks`, as the module says."""
    kind = "COALESCE(b.meta ->> 'event_type', CASE b.content {} END)".format(
        " ".join(f"WHEN '{text}' THEN '{code}'" for text, code in UNTYPED.items())
    )
    op.execute(f"""
        CREATE TEMP TABLE leaving ON COMMIT DROP AS
        SELECT b.id, {kind} AS kind
        FROM blocks b
        WHERE b.kind = 'event'
          AND b.author_type = 'platform'
          AND {kind} IN ({_quoted(MOVED)})
          AND (b.meta ->> 'agent_notice') IS NULL
          AND NOT EXISTS (SELECT 1 FROM threads t WHERE t.root_block_id = b.id)
    """)
    op.execute(f"""
        INSERT INTO run_records (
            id, project_id, conversation_id, turn_id, seat, kind, severity,
            content, meta, created_at, updated_at
        )
        SELECT
            b.id,
            b.project_id,
            CASE WHEN l.kind IN ({_quoted(UNATTACHED)}) THEN NULL
                 ELSE b.conversation_id END,
            b.turn_id,
            COALESCE(
                b.meta ->> 'seat',
                CASE WHEN b.author NOT IN ({_quoted(NOT_A_SEAT)}) THEN b.author END
            ),
            l.kind,
            COALESCE(b.meta ->> 'severity', 'info'),
            b.content,
            CASE WHEN l.kind IN ({_quoted(UNATTACHED)})
                 THEN (COALESCE(b.meta::jsonb, '{{}}'::jsonb)
                       || jsonb_build_object(
                            'conversation', b.conversation_id::text,
                            'event_type', l.kind))::json
                 ELSE (COALESCE(b.meta::jsonb, '{{}}'::jsonb)
                       || jsonb_build_object('event_type', l.kind))::json
            END,
            b.created_at,
            b.updated_at
        FROM blocks b
        JOIN leaving l ON l.id = b.id
        WHERE b.created_at >= now() - interval '30 days'
          AND NOT (
              l.kind = 'turn_queued'
              AND b.created_at BETWEEN '{STORM[0]}' AND '{STORM[1]}'
          )
    """)
    op.execute("DELETE FROM blocks WHERE id IN (SELECT id FROM leaving)")
    op.execute("DROP TABLE leaving")


def downgrade() -> None:
    op.drop_index("ix_blocks_machine_events", table_name="blocks")
    op.create_index(
        "ix_blocks_machine_events",
        "blocks",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text(
            "(meta ->> 'event_type') IN ('machine_provisioning', 'device_waiting',"
            " 'sandbox_rebuilt', 'environment_repaired')"
        ),
    )
    op.create_index(
        "ix_blocks_cloud_provisioning",
        "blocks",
        ["conversation_id", "created_at", "id"],
        postgresql_where=sa.text("(meta ->> 'event_type') = 'cloud_provisioning'"),
    )
    op.drop_table("run_records")
