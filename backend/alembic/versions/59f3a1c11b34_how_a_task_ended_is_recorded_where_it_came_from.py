"""How a task ended is recorded where it came from, as an event

Revision ID: 59f3a1c11b34
Revises: f02ff6f94b03
Create Date: 2026-11-14

When a task closed, the conversation it came from (its channel's main line, or
the 支线 it came from) was told how it ended as a message of the task's AI
teammate: 「任务「{任务名}」已关闭：{结论}」, 「{任务名}」已采纳 · PR #{n},
「{任务名}」已采纳 and 「任务「{任务名}」已关闭」, with the task in
``meta.task_result``. From this release the platform records it as its own line
(`review.task_landing.tell_origin_ended`), like a version handed in or returned
(`f02ff6f94b03`): ``kind = 'event'``, author ``system``, ``event_type``
``task_ended``, the task in ``meta.task_id``. The AI teammate that opened the
task from the main line now says the result in its own words, and a line under
its name would say it twice. The sentence and its key stay as they were.

Every line written the old way is turned into the new one in place: same id,
same number (``seq``) and date, so the conversation keeps its order and
nobody's read cursor moves. Unread counts only messages, so a line that stops
being one can only lower a count. A line in a 支线 was counted as a reply when
it was written (trigger ``blocks_thread_replied``); the 支线's count and last
reply date are set back to what its messages say.

A line saying a step landed and the task goes on (``taskStepLanded``) is not
about the end and stays a message. A line is known by the key of its sentence,
which every one has carried since #5260. One written before that has no key;
the only such lines are these and the step ones, whose sentence has always
ended 「，任务继续进行」, so a keyless line that does not end so is one of
these. Before #5260 the last step's merge was also told this way
(「{任务名}」已采纳 · PR #{n}) before the close was; that line is about the
end too and is turned with it.

The release still serving while this runs writes the old shape; a line it
writes between this migration and the switch keeps that shape. Idempotent:
only messages are turned, so a second run finds none.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "59f3a1c11b34"
down_revision: str | Sequence[str] | None = "f02ff6f94b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The keys of the sentences that said how a task ended
#: (`review.task_landing._how_it_ended`).
_ENDED_KEYS = "('taskCompleted', 'taskLanded', 'taskLandedNoPr', 'taskClosed')"

#: A line about a task saying how it ended, over a block aliased ``{b}``.
_ENDED = """
    {b}.meta ->> 'task_result' IS NOT NULL
    AND (
        {b}.meta -> 'i18n' -> 'content' ->> 'key' IN {keys}
        OR (
            {b}.meta -> 'i18n' -> 'content' ->> 'key' IS NULL
            AND {b}.content NOT LIKE '%，任务继续进行'
        )
    )
"""

#: The lines to turn: a message about a task saying how it ended.
_OLD_LINE = "b.kind = 'message' AND " + _ENDED.format(b="b", keys=_ENDED_KEYS)


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
                  AND NOT ({_ENDED.format(b="r", keys=_ENDED_KEYS)})
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
                (b.meta::jsonb - 'task_result')
                || jsonb_build_object(
                    'event_type', 'task_ended',
                    'severity', 'info',
                    'who', 'platform',
                    'detail', NULL,
                    'detail_label', NULL,
                    'task_id', b.meta ->> 'task_result'
                )
            )::json
        WHERE {_OLD_LINE}
        """
    )


def downgrade() -> None:
    """The lines stay events: the release before this one draws an event line
    as the platform's, which is what they are."""
