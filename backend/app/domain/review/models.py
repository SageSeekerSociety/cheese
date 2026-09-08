"""Accept card model — spec §4.4, §6.3, eval C5.

When a topic's main deliverable is ready, 芝士 hands the accept card to a
specific person ("等 XX 验收"), not a broadcast. Hard rule (spec §4.4): in
collaborative mode AI cannot accept its own work. Accept is recorded by name and
revocable; accepting = merge + archive the topic.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk
from app.domain.review.notes import NoteCode


class AcceptStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked = "revoked"
    # 采纳时 merge 冲突：话题不归档、卡片进入此状态，芝士被派去解决冲突，
    # 解决后由人重试采纳（spec §6.3 冲突处理：芝士先尝试解决）。
    conflict = "conflict"
    # 机器闸门 (spec §4.4/§9, eval C2): the project's check_command is running
    # in the topic's workspace; the card reaches the reviewer only when green.
    pending_gate = "pending_gate"
    # 检查红了：卡片作废（不递给验收人），芝士收到系统 nudge 去修，修完重新递卡。
    gate_failed = "gate_failed"
    # 闸门没跑成（2026-08-11）：检查本身没能在门禁容器里跑起来（工具链缺失/
    # 装不上、Docker 起不来），所以它对代码**没有结论**。既不是绿也不是红：
    # 卡照样不递给验收人，但话术和卡面都要说"没跑成"而不是"没通过"——把它
    # 当绿放行，正是这个状态存在的原因（见 .claude/scripts/check.sh 的 exit 2）。
    gate_blocked = "gate_blocked"
    # 采纳=授权、轮询器等绿再合（#422）的在途态。#718 撤销了授权语义（采纳
    # 回到当场合并），存量行也已由迁移 b1e6a4d2c718 收敛 —— 枚举值照 gate_*
    # 先例保留、死于写。
    pr_open = "pr_open"


class GateOutcome(enum.StrEnum):
    """What a quality-gate run actually established (2026-08-11).

    Not a bool: "the check ran and disliked the code" and "the check never ran"
    are different facts, and collapsing them is how a gate ends up green on an
    environment where nothing but lint could start. `check_command` reports the
    third one with exit code 2. Nothing produces these two any more (the gate
    was retired by #296); historical rows still carry them and must render.
    """

    passed = "passed"
    failed = "failed"
    blocked = "blocked"


class AcceptCard(UuidPk, Timestamps, Base):
    __tablename__ = "accept_cards"

    # The room the card is read in. A card filed for a piece of work names its
    # thread below; a card filed for the room itself leaves that NULL.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 这张卡交付的是哪一棵树 —— 一棵树 = 一个分支 = 一个 PR = 一批活. The room
    # is where the card is READ; the tree is what it delivers, and with more
    # than one tree per room those stop being the same answer. "One card at a
    # time" is a rule about not running two PRs on one branch, so it is scoped
    # here rather than to the room — scoping it to the room would mean a room
    # could never open a second PR, which is what a second tree was for.
    #
    # Nullable: `SET NULL`, so a card outlives the tree it delivered, and a
    # historical card whose tree was never created has no honest value.
    tree_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_trees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 这批交付是哪几条活干出来的 —— 递卡的那一刻，由递卡方说 (#189)。The ids of
    # `tasks` rows, and the whole of what `Cheese-Task:` writes into permanent
    # history.
    #
    # It is DECLARED and not derived, because nothing here can derive it. A
    # task's `tree_id` is fixed when `cheese split` runs and says which batch it
    # joined THEN; which branch its code ends up on is decided when the room
    # files a card, and a room that keeps working across two batches makes those
    # two different answers. Enumerating the delivering tree's members therefore
    # credits whoever happened to be sitting on that tree: run it over this
    # project's own room/task/tree data as of 2026-09-08 and one delivery comes
    # out wrong in both directions — three tasks that contributed nothing named
    # on a PR, and the task that actually wrote it named on the previous one.
    # (Nothing in this repository's history carries a wrong trailer; the
    # trailers did not exist when those PRs merged. What is wrong is the
    # inference, measured against real data before it could write anything.)
    # Commit authors identify the room's agent, not its individual tasks.
    #
    # There is no automatic filling-in, and that is the point. Every rule a
    # machine could apply — the tree's members, "everything not claimed by an
    # earlier card" — establishes only that a task EXISTS and was not filed
    # before; neither can establish that its code is in this diff. A placeholder
    # task that wrote no code, and a sibling still running whose work goes out
    # next batch, both pass those tests and would be signed onto a change they
    # contributed nothing to. So an undeclared delivery carries no
    # `Cheese-Task:` line at all: a wrong name in permanent history is worse
    # than no name, because an audit believes it.
    #
    # A JSON list rather than a join table for the same reason `nudge_state`
    # is one: it is read and written whole, always by the card that owns it, and
    # never queried across cards.
    delivered_task_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # Routed reviewer (spec C5): the specific person asked to accept.
    reviewer_handle: Mapped[str] = mapped_column(String(64), index=True)
    # Why this reviewer was suggested (最懂/没参与/有空), for transparency.
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    # What this topic CHANGED, in the words of whoever did the work — the one
    # description that survives into permanent history. `change_subject` is a
    # Conventional Commits subject (`fix(api): …`, imperative, ≤72 chars) and
    # `change_body` is the why. They become the PR title/body AND the squash
    # commit that lands on the default branch, so the project's git log stops
    # reading "采纳 <话题标题> (#213)" — a room name, not a change description.
    # NULL on cards filed without them (and on every card that predates the
    # columns): review/services.py falls back to the topic title.
    change_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AcceptStatus] = mapped_column(
        Enum(AcceptStatus, native_enum=False, length=16),
        default=AcceptStatus.pending,
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    # 卡此刻停在什么上 (review/notes.py)。`note` 是给人看的一句话，这一列是给代码
    # 看的状态——两者分开的理由：它们过去是同一个字段，判断靠 `note.startswith(带
    # emoji 的前缀)`，于是改一句文案就能改掉一次判断，而没有任何东西会红。NULL =
    # 这条 note 只是一句交代，没有代码要据此分支。
    note_code: Mapped[NoteCode | None] = mapped_column(
        Enum(NoteCode, native_enum=False, length=32), nullable=True
    )
    # 平台已经替这张卡自动换过几次基。封顶用 (review/services.py)：换基换不上来说
    # 明 main 移动得比 CI 还快，得叫人。曾经是数 `note` 里 `⟲` 的个数，而 `note`
    # 每被别的状态覆写一次、每被换基自己推动 head 一次就清空——计数器归零，上限
    # 永远够不着，平台无限换基下去。
    rebase_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 机器闸门 (eval C2): when the project's check_command STARTED running for
    # this card. Deliberately not "when the card was filed" (that is
    # `created_at`) — the gap between the two is queueing + worktree
    # preparation, and a `pending_gate` card with this still NULL never got as
    # far as running the check at all. See review/gate_sweep.py, which uses
    # COALESCE(gate_started_at, created_at) as its clock.
    gate_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 机器闸门 (eval C2): when the project's check_command passed for this card.
    gate_passed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Tail of the check output (green or red) — full output is in the gate log.
    gate_output: Mapped[str] = mapped_column(Text, default="", server_default="")
    # PR-based accept (#188 §5.1 → #296 → #718): the real GitHub PR this card
    # rides on. `pr_number`/`pr_url` are set by pr_publish.py when the card is
    # filed (or by the accept-time retry `_publish_pr_for_accept`). A card
    # WITH a pr_number rides a PR; one without is an unbound project's card
    # (the platform is its forge, #363) or a discussion-only topic — every
    # card is self-describing, so GitHub outages never strand one.
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Which repo the PR lives in (#192 project_git_installations, not
    # necessarily the same as the `upstream` git remote). Backfilled by the
    # poller's first look at a card it wasn't recorded on.
    pr_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The PR head as this platform last saw it — the commit the card shows,
    # and therefore THE sha the accept click hands to the merge API ("SHA that
    # pull request head must match", #718): a push that lands between the
    # human's look and the merge makes GitHub answer 409 instead of merging a
    # commit nobody saw.
    pr_head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # None: not merged. Set: when the PR merged (by the accept click, by the
    # armed auto-merge, or by a human on GitHub directly).
    pr_merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 卡上的状态＝合并态 (#718)：轮询器每拍把 verdict 镜像到这里 ——
    # {"state","who","reasons":[{kind,checks,detail}],"head_sha","checked_at",
    # "since"}。`since` 是「这个 (state, head) 组合从什么时候开始成立」，给
    # 「必跑检查迟迟没报到，超过宽限转人」那条当时钟。NULL = 轮询器还没看过
    # （或这张卡不骑 PR）。展示走它，采纳点击不走 —— 点击现场重算。
    merge_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 绿了自动合 (#718，GitHub auto-merge 的对应物；项目开了 auto_merge_allowed
    # 才可用)：布防不是决议 —— 卡留在 pending，规则满足时轮询器以布防人的名义
    # 合并并把布防人的那票算进去；新提交作废采纳（dismiss_stale）同样解除布防。
    auto_merge_armed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_merge_armed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # PR 回流的去重账本 (review/pr_signals.py)：每一类回流（CI 失败 / 评审意见 /
    # 合并冲突）各记「发到哪个内容签名」和「发过几轮」。
    #
    # 为什么是一列而不是内存：这本账要活过后端重启。签名只活在进程里的话，一次
    # 重启就把所有在飞的 PR 重新叫一遍 —— 每张卡一条重复的「CI 挂了」，而 CI 一
    # 个字都没变。
    #
    # 为什么是 JSON 而不是几个列：它整本一起读、一起写，而且类别还会加（今天三
    # 类）。加一类不该等于一次迁移。
    nudge_state: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )


class AcceptApproval(UuidPk, Timestamps, Base):
    """主分支保护 (spec §4.4): one human vote toward a card's accept.

    A card needs `approvals_required` (Project.settings) distinct approvals
    before accept actually merges. Votes are history — revoking an accept does
    not clear them.
    """

    __tablename__ = "accept_approvals"
    __table_args__ = (
        UniqueConstraint("card_id", "approver_handle", name="uq_accept_approval"),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accept_cards.id", ondelete="CASCADE"), index=True
    )
    approver_handle: Mapped[str] = mapped_column(String(64))
