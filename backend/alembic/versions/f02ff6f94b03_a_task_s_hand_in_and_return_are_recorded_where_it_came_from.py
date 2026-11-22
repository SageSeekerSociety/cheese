"""A task's hand-in and return are recorded where it came from, as events

Revision ID: f02ff6f94b03
Revises: a7c3e9f1b5d2
Create Date: 2026-11-14

A version handed in for review, and a version returned, used to be written into
the conversation the task came from (its channel's main line, or the 支线 it
came from) as a message of the task's AI teammate: 「{任务名}」已提交审阅 with
the card it filed in ``meta.delivery``, and 「{谁} 退回了「{任务名}」」
(`9aac1f78ad6d` backfilled the first kind for versions then waiting). The room
drew them under the teammate's name and avatar, as if it had said them. They are
things that happened, and from this release the platform records them as its
own lines (`review.task_landing.record_at_origin`): ``kind = 'event'``, author
``system``, ``event_type`` ``task_filed`` / ``task_returned``, the task in
``meta.task_id``, a hand-in's card and version in ``meta.delivery``. The
sentence and its key stay as they were; a hand-in's card shows the task's name,
which its sentence already holds.

Every line written the old way is turned into the new one in place: same id,
same number (``seq``) and date, so the conversation keeps its order and nobody's
read cursor moves. Unread counts only messages, so a line that stops being one
can only lower a count, never raise it. A line in a 支线 was counted as a reply
when it was written (trigger ``blocks_thread_replied``); the 支线's count and
last reply date are set back to what its messages say.

The line saying how a task ended (``taskCompleted`` and its kin) is the
teammate's own words and stays a message.

The release still serving while this runs writes the old shape; a line it writes
between this migration and the switch keeps that shape. Idempotent: only
messages are turned, so a second run finds none.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f02ff6f94b03"
down_revision: str | Sequence[str] | None = "a7c3e9f1b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The lines to turn: a message about a task saying it was handed in or returned.
_OLD_LINE = """
    b.kind = 'message'
    AND b.meta ->> 'task_result' IS NOT NULL
    AND b.meta -> 'i18n' -> 'content' ->> 'key' IN ('taskFiledForReview', 'taskReturned')
"""


def upgrade() -> None:
    # The 支线 first, while the lines still read as messages: each one's count
    # loses the lines turned in it, and its last reply is its newest message
    # that stays one.
    op.execute(
        f"""
        WITH turned AS (
            SELECT b.conversation_id, count(*) AS n
            FROM blocks b
            WHERE {_OLD_LINE}
            GROUP BY b.conversation_id
        )
        UPDATE threads t
        SET reply_count = greatest(t.reply_count - turned.n, 0),
            last_reply_at = (
                SELECT max(r.created_at) FROM blocks r
                WHERE r.conversation_id = t.id
                  AND r.kind = 'message'
                  AND NOT (
                      r.meta ->> 'task_result' IS NOT NULL
                      AND r.meta -> 'i18n' -> 'content' ->> 'key'
                          IN ('taskFiledForReview', 'taskReturned')
                  )
            )
        FROM turned
        WHERE t.id = turned.conversation_id
        """
    )
    op.execute(
        f"""
        UPDATE blocks b
        SET kind = 'event',
            author_type = 'platform',
            author = 'system',
            meta = (
                (b.meta::jsonb - 'task_result' - 'delivery')
                || jsonb_build_object(
                    'event_type',
                    CASE b.meta -> 'i18n' -> 'content' ->> 'key'
                        WHEN 'taskFiledForReview' THEN 'task_filed'
                        ELSE 'task_returned'
                    END,
                    'severity', 'info',
                    'who', 'platform',
                    'detail', NULL,
                    'detail_label', NULL,
                    'task_id', b.meta ->> 'task_result'
                )
                || CASE
                    WHEN b.meta -> 'delivery' IS NOT NULL
                    THEN jsonb_build_object(
                        'delivery', jsonb_build_object(
                            'card_id', b.meta -> 'delivery' -> 'card_id',
                            'version', b.meta -> 'delivery' -> 'version'
                        )
                    )
                    ELSE '{{}}'::jsonb
                END
            )::json
        WHERE {_OLD_LINE}
        """
    )


def downgrade() -> None:
    """The lines stay events: the release before this one draws an event line
    as the platform's, which is what they are."""
