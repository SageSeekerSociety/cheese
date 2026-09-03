"""accept card: note_code and rebase_count

A card's `note` used to carry its state as well as its wording: the code
recognised a state by `note.startswith(<emoji 前缀>)`, and counted automatic
rebases with `note.count("⟲")`. Both now live in columns.

The backfill is the last place a prefix is ever parsed. Cards in flight carry
live state in their prose — a paused poller, a card the platform refuses to
merge — and dropping it would restart those state machines from zero.

Revision ID: b3e1d75a4c20
Revises: d5a2f70c9b18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e1d75a4c20"
down_revision: str | Sequence[str] | None = "d5a2f70c9b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Longest first: `⚠️ 采纳未完成：…` must be tried before anything that would
# also match a bare `⚠️`, or a specific state backfills as a generic one.
_PREFIXES: list[tuple[str, str]] = [
    ("⚠️ 采纳未完成：无法为这张卡开 PR", "accept_pr_open_failed"),
    ("⚠️ 采纳未完成：PR 未能合并", "accept_pr_stalled"),
    ("⚠️ 平台自动重推失败", "repush_failed"),
    ("⚠️ 未走 PR 采纳", "pr_skipped"),
    ("⚠️ 开 PR 失败", "pr_open_failed"),
    ("⚠️ 轮询暂停", "poll_paused"),
    ("⚠️ CI 检查未通过：", "checks_failed"),
    ("🌿 本地分支与 PR 分支已分叉", "repush_diverged"),
    ("⏱ 闸门没跑完", "gate_abandoned"),
    ("🗑 卡片已作废", "voided"),
    ("⏳ 等 CI", "waiting_checks"),
    ("🔨 人工放行", "force_merged"),
    ("🚫", "merge_refused"),
    ("✋", "merge_withheld"),
    ("🚪", "pr_closed_unmerged"),
    ("📦", "archived"),
]


def upgrade() -> None:
    op.add_column(
        "accept_cards", sa.Column("note_code", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "accept_cards",
        sa.Column(
            "rebase_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )

    conn = op.get_bind()
    # Reverse order so the first entry in `_PREFIXES` wins: each statement
    # overwrites what the previous ones set, which is how "longest match" is
    # expressed without a CASE over sixteen branches.
    for prefix, code in reversed(_PREFIXES):
        conn.execute(
            sa.text(
                "UPDATE accept_cards SET note_code = :code WHERE note LIKE :pattern"
            ),
            {"code": code, "pattern": prefix.replace("%", r"\%") + "%"},
        )
    # A merge conflict's note is the raw git output, so it has no prefix to
    # recognise — the status is what identifies it.
    conn.execute(
        sa.text(
            "UPDATE accept_cards SET note_code = 'merge_conflict' "
            "WHERE status = 'conflict' AND note <> ''"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE accept_cards "
            "SET rebase_count = length(note) - length(replace(note, '⟲', '')) "
            "WHERE note LIKE '%⟲%'"
        )
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "rebase_count")
    op.drop_column("accept_cards", "note_code")
