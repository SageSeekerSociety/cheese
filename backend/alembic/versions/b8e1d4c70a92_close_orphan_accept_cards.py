"""close accept cards stranded on archived topics (孤儿卡, 2026-08-10)

Data-only migration — no schema change. Before this, `TopicService._archive_one`
did not touch accept cards, so archiving a topic left its card on a non-terminal
status forever. For `pr_open` cards that was not "stopped" but "still running":
`SchedulerService.poll_open_prs` selected purely on the CARD's status, so every
60s it kept driving them with the approver's GitHub token.

The code fix (review/archive.py + AcceptCardRepository.list_pr_open_on_active_topics)
stops it happening again; this closes the rows that already exist. Same three
rules as the runtime path, so the data ends up in exactly one shape:

  * pr_open + pr_merged_at IS NOT NULL -> accepted (the PR really did land in
    main; archiving just cut the deploy watch short)
  * pr_open + pr_merged_at IS NULL     -> revoked (PR still open on GitHub; the
    platform deliberately does NOT close it — see review/archive.py's docstring)
  * pending / pending_gate / conflict  -> revoked

`gate_failed` / `accepted` / `rejected` / `revoked` are already terminal and are
left alone, which also makes this idempotent: re-running matches nothing.

`decided_by` / `decided_at` are only filled where NULL — on a `pr_open` card they
record who authorised the PR, and overwriting that would erase the audit trail.

Revision ID: b8e1d4c70a92
Revises: d4a1b6f27c90
Create Date: 2026-08-10 00:00:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e1d4c70a92"
down_revision: str | Sequence[str] | None = "d4a1b6f27c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.close_orphan_accept_cards")

# The set the runtime path calls OPEN_CARD_STATUSES (review/archive.py).
_OPEN_STATUSES = ("pending", "pending_gate", "conflict", "pr_open")

_COUNT_SQL = sa.text(
    """
    SELECT c.status, count(*) AS n
      FROM accept_cards c
      JOIN topics t ON t.id = c.topic_id
     WHERE t.status = 'archived'
       AND c.status = ANY(:statuses)
     GROUP BY c.status
     ORDER BY c.status
    """
)

_SETTLE_MERGED_SQL = sa.text(
    """
    UPDATE accept_cards AS c
       SET status = 'accepted',
           note = left(
               '📦 话题归档收尾：PR #' || coalesce(c.pr_number::text, '?')
               || ' 已合并，部署结果不再跟踪。'
               || CASE WHEN btrim(coalesce(c.note, '')) = '' THEN ''
                       ELSE E'\\n' || btrim(c.note) END,
               2000),
           decided_at = coalesce(c.decided_at, now())
      FROM topics AS t
     WHERE t.id = c.topic_id
       AND t.status = 'archived'
       AND c.status = 'pr_open'
       AND c.pr_merged_at IS NOT NULL
    """
)

_REVOKE_STRANDED_PR_SQL = sa.text(
    """
    UPDATE accept_cards AS c
       SET status = 'revoked',
           note = left(
               '📦 话题归档，平台已停止推进 PR #' || coalesce(c.pr_number::text, '?')
               || '。PR 未合并、仍开在 GitHub 上，需要人工决定合并还是关闭：'
               || coalesce(c.pr_url, '(无链接)')
               || CASE WHEN btrim(coalesce(c.note, '')) = '' THEN ''
                       ELSE E'\\n' || btrim(c.note) END,
               2000),
           decided_at = coalesce(c.decided_at, now())
      FROM topics AS t
     WHERE t.id = c.topic_id
       AND t.status = 'archived'
       AND c.status = 'pr_open'
       AND c.pr_merged_at IS NULL
    """
)

_REVOKE_UNDECIDED_SQL = sa.text(
    """
    UPDATE accept_cards AS c
       SET status = 'revoked',
           note = left(
               '📦 话题归档，验收卡随之关闭（原状态：' || c.status || '）。'
               || CASE WHEN btrim(coalesce(c.note, '')) = '' THEN ''
                       ELSE E'\\n' || btrim(c.note) END,
               2000),
           decided_at = coalesce(c.decided_at, now())
      FROM topics AS t
     WHERE t.id = c.topic_id
       AND t.status = 'archived'
       AND c.status IN ('pending', 'pending_gate', 'conflict')
    """
)


def close_orphan_cards(conn) -> dict[str, int]:
    """Run the sweep on ``conn``. Returns {"before": …, per-rule counts, "after": …}.

    Exposed as a plain function (not inlined into ``upgrade``) so the test suite
    can execute the very SQL that ships, instead of a re-typed copy of it.
    """
    before = {
        row.status: row.n
        for row in conn.execute(_COUNT_SQL, {"statuses": list(_OPEN_STATUSES)})
    }
    settled = conn.execute(_SETTLE_MERGED_SQL).rowcount
    stranded = conn.execute(_REVOKE_STRANDED_PR_SQL).rowcount
    undecided = conn.execute(_REVOKE_UNDECIDED_SQL).rowcount
    after = {
        row.status: row.n
        for row in conn.execute(_COUNT_SQL, {"statuses": list(_OPEN_STATUSES)})
    }
    return {
        "before": before,
        "settled_merged_pr": settled,
        "revoked_open_pr": stranded,
        "revoked_undecided": undecided,
        "after": after,
    }


def upgrade() -> None:
    result = close_orphan_cards(op.get_bind())
    logger.info("close_orphan_accept_cards: %s", result)
    print(f"close_orphan_accept_cards: {result}")


def downgrade() -> None:
    """Not reversible: the pre-archive status of each card is not recorded
    anywhere else, and reviving them would put the poller back on archived
    topics — the exact bug this closes. No-op on purpose."""
