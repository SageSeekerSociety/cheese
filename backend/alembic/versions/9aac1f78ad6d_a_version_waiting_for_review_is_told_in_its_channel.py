"""A version waiting for review is told in its channel

Revision ID: 9aac1f78ad6d
Revises: e9d4b023c781
Create Date: 2026-11-13

From this release, handing a version of a task in for review leaves one line in
the channel's main line the task was opened from: 「{任务名}」已提交审阅, from the
task's AI teammate, carrying the card it filed (`review.task_landing.tell_origin`,
``meta.delivery``). The channel's 「待你审阅」 bar and its review pane open from
that line. A version handed in before the release has no line, so a reviewer
waiting on it would find nothing to open it from in the channel.

So, for every card still waiting for review on an open task that was opened
from a message in its channel's main line, and that has no such line yet, one
line is written as the release writes it, dated when the card was filed. That
is where the release's own line would sit in the main line, and a line dated in
the past moves no channel up the sidebar (its last activity is the newest date).

Left at that, the line would light the channel for everyone: unread counts
blocks by number (`TopicReadState.last_read_seq`), and a new block takes the
conversation's next number however old its date. So a person whose cursor
stood on the main line's newest block before the lines were written is moved
past them: they had read everything, and still have. A person behind keeps
their place. Nothing calls a teammate (no ``agent_recipient``), so nothing
wakes; a teammate's read position is a date, which the line's date is already
behind for any teammate that has read since the card was filed.

What the release narrows, and this keeps: the line goes to the channel's main
line only. A task opened inside a 支线 is told in that 支线, which the release
opens under the message when it has none; that is left to the release's next
line about the task, not written here.

The teammate the line is from is the task's own seat when that seat is a
teammate of the project, else the project's default teammate — the order
`AgentInstanceService.for_task` resolves, without its middle step (the room's
roster seat), which a pending task with no seat of its own does not reach in
practice. A project with neither gets no line, as the release writes none.

The release still serving writes no delivery line and never reads one. The
statement is idempotent: run again, every card it covered has its line.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9aac1f78ad6d"
down_revision: str | Sequence[str] | None = "e9d4b023c781"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH waiting AS (
            SELECT
                c.id AS card_id,
                c.created_at AS filed_at,
                coalesce(c.change_subject, c.deliverable_name) AS subject,
                (
                    SELECT count(*) FROM accept_cards earlier
                    WHERE earlier.task_id = c.task_id
                      AND (earlier.created_at, earlier.id) <= (c.created_at, c.id)
                ) AS version,
                t.id AS task_id,
                t.project_id,
                t.room_id,
                t.title,
                t.title_source,
                coalesce(
                    (
                        SELECT t.agent_handle FROM agent_instances own
                        WHERE own.project_id = t.project_id
                          AND 'cheese-' || left(replace(own.id::text, '-', ''), 12)
                              = t.agent_handle
                        LIMIT 1
                    ),
                    'cheese-'
                    || left(replace(p.default_agent_instance_id::text, '-', ''), 12)
                ) AS teammate
            FROM accept_cards c
            JOIN tasks t ON t.id = c.task_id
            JOIN projects p ON p.id = t.project_id
            JOIN topics room ON room.id = t.room_id
            JOIN blocks origin ON origin.id = t.upgraded_from_block_id
            JOIN blocks source ON source.id = t.source_block_id
            WHERE c.status = 'pending'
              AND t.status = 'open'
              AND room.status <> 'archived'
              AND origin.kind = 'message'
              AND origin.conversation_id = t.room_id
              AND source.conversation_id = t.room_id
              -- A 支线 that became the task in place tells nobody about itself.
              AND NOT EXISTS (SELECT 1 FROM threads th WHERE th.id = t.id)
              AND NOT EXISTS (
                  SELECT 1 FROM blocks said
                  WHERE said.conversation_id = t.room_id
                    AND said.meta ->> 'task_result' = t.id::text
                    AND said.meta -> 'delivery' ->> 'card_id' = c.id::text
              )
        ),
        titled AS (
            SELECT
                w.*,
                CASE WHEN w.title_source = 'placeholder' THEN '新任务' ELSE w.title END
                    AS said_title,
                CASE
                    WHEN w.title_source = 'placeholder'
                    THEN json_build_object('key', 'taskUntitled', 'params', '{}'::json)
                    ELSE to_json(w.title)
                END AS title_param
            FROM waiting w
            WHERE w.teammate IS NOT NULL
        ),
        written AS (
            INSERT INTO blocks (
                id, project_id, conversation_id, kind, author_type, author,
                content, refs, meta, created_at, updated_at
            )
            SELECT
                gen_random_uuid(), project_id, room_id, 'message', 'participant',
                teammate,
                '「' || said_title || '」已提交审阅',
                '[]'::json,
                json_build_object(
                    'task_result', task_id::text,
                    'delivery', json_build_object(
                        'card_id', card_id::text,
                        'subject', subject,
                        'version', version
                    ),
                    'i18n', json_build_object(
                        'content', json_build_object(
                            'key', 'taskFiledForReview',
                            'params', json_build_object('title', title_param)
                        )
                    )
                ),
                filed_at, filed_at
            FROM titled
            ORDER BY room_id, filed_at
            RETURNING conversation_id, seq
        ),
        numbered AS (
            SELECT conversation_id, min(seq) AS first, max(seq) AS last
            FROM written
            GROUP BY conversation_id
        )
        -- The block numbering lock (`block_numbered`) is held per conversation
        -- until this migration commits, so the lines written into one channel
        -- are numbered back to back: whoever stood on the number before the
        -- first of them had read the whole main line.
        UPDATE topic_read_states r
        SET last_read_seq = n.last
        FROM numbered n
        WHERE r.topic_id = n.conversation_id
          AND r.last_read_seq = n.first - 1
        """
    )


def downgrade() -> None:
    """The lines stay: a line the release would have written reads exactly
    like one it did write, and the release before this one shows any message
    in a channel as the message it is."""
