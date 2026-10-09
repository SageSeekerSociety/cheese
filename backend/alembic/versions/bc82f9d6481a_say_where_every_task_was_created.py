"""Every task started in a room says so on the room's line

Revision ID: bc82f9d6481a
Revises: 7d3a91c5e2b4
Create Date: 2026-10-09

A task started in a room (not made from a message) leaves a row on the room's
main line, `action: task_created`, where the room sees it begin. Tasks from
before that row was written have none, and the client made up for it by
reading the room's whole task list and placing a marker where each would have
been. This writes the row those tasks never got, so the line holds its own
history and the client no longer needs the list.

The row is the one `TopicService._card_block` writes today, at the moment the
task was created. A task that already has any row naming it on its room's line
(`meta.task_id`: a `task_created` row, or the `split` row of the weeks before)
is left alone, so running this twice writes nothing the second time. A task made
from a message shows under that message and gets no row.

The sentence is copied, not imported (`.claude/rules/migrations.md` rule 8):
`taskCreated` from frontend/src/i18n/messages/zh-CN/roomNotice.json, its key and
parameters in `meta.i18n.content` as `app.core.sentences.with_keys` stores them,
and the unnamed-task rule of `app.domain.room_task.services.said_title` (the
`taskUntitled` sentence, 「新任务」, when `title_source` is `placeholder`). A task
with no recorded creator is said to be the platform's (`<@system>`), as
`_card_block` says it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bc82f9d6481a"
down_revision: str | Sequence[str] | None = "7d3a91c5e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = """
WITH announced AS (
    SELECT DISTINCT conversation_id, (meta ->> 'task_id') AS task_id
    FROM blocks
    WHERE (meta ->> 'task_id') IS NOT NULL
),
missing AS (
    SELECT
        t.id,
        t.project_id,
        t.room_id,
        t.created_at,
        '<@' || COALESCE(t.created_by, 'system') || '>' AS actor,
        CASE WHEN t.owner_handle IS NOT NULL THEN '<@' || t.owner_handle || '>'
             ELSE '' END AS owner,
        t.title_source = 'placeholder' AS unnamed,
        t.title
    FROM tasks t
    WHERE t.upgraded_from_block_id IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM announced a
          WHERE a.conversation_id = t.room_id AND a.task_id = t.id::text
      )
)
INSERT INTO blocks (
    id, project_id, conversation_id, kind, author_type, author, content, refs,
    meta, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    m.project_id,
    m.room_id,
    'event',
    'platform',
    'system',
    m.actor || ' 创建了任务「' || CASE WHEN m.unnamed THEN '新任务' ELSE m.title END
        || '」，由 ' || m.owner || ' 负责',
    CAST('[]' AS json),
    json_build_object(
        'platform', true,
        'action', 'task_created',
        'task_id', m.id::text,
        'i18n', json_build_object(
            'content', json_build_object(
                'key', 'taskCreated',
                'params', json_build_object(
                    'actor', m.actor,
                    'title', CASE
                        WHEN m.unnamed THEN json_build_object(
                            'key', 'taskUntitled', 'params', json_build_object()
                        )
                        ELSE to_json(m.title)
                    END,
                    'owner', m.owner
                )
            )
        )
    ),
    m.created_at,
    m.created_at
FROM missing m
"""


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    """The rows stay: they say what happened, and nothing reads their absence."""
