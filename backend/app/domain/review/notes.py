"""验收卡 note 的状态码，和唯一的写入口。

一张卡的 `note` 过去一个字段干两件事：给人看的一句话，和给代码看的状态。代码那
一半只能靠**前缀**认出来，而前缀是以 emoji 开头的字符串——于是「这条 note 意味着
什么」散落在 13 处 `startswith`、一处 `count("⟲")`，和浏览器里一份硬编码的 emoji
数组里。三者都会跟文案一起过期：改一句话就可能改掉一次判断，而没有任何东西会红。

现在状态住在 `AcceptCard.note_code` 里，`note` 只剩「给人看的一句话」。判断读码，
渲染读码算出来的级别，文案怎么写都不影响任何一方。和 `platform_failures.py` 的
code + copy contract 同一条规矩：文案在外，判断在内，中间传的是码不是字符。

**写 note 一律走 `record` / `annotate` / `clear`**，别直接赋值 `card.note`——那正是
让文案和状态分家的那个动作。
"""

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard

#: `note` 列的上限。截断在写入口做一次，调用方不必自己切。
NOTE_MAX = 2000


class NoteLevel(enum.StrEnum):
    """这条 note 说的是「停住了」还是「还在走」。"""

    #: 需要人或芝士动手，卡不会自己往前走。
    error = "error"
    #: 平台还在推进，或者只是一条留痕。
    info = "info"


class NoteCode(enum.StrEnum):
    """卡此刻停在什么上。**只有被读到、或需要渲染成红的状态才配一个码**——

    一句纯给人看的交代（合并成功了、被驳回了、分支推哪去了）不进这张表，它的码是
    `None`，级别按 info 走。按句子发码会让这个枚举变成文案表的影子，而影子是会和
    本体走散的。
    """

    # —— 停住了：需要人或芝士动手 ——

    #: 芝士的修复没能推上 GitHub，PR 上的红是旧的。
    repush_failed = "repush_failed"
    #: 本地话题分支与 PR 分支已分叉，平台不替它强推覆盖 PR 上的提交。
    repush_diverged = "repush_diverged"
    #: 轮询用的 token 失效，卡不再自己推进。
    poll_paused = "poll_paused"
    #: PR 的检查没通过，已经叫过芝士来修。
    checks_failed = "checks_failed"
    #: 检查全绿，但 GitHub 拒绝合并。
    merge_refused = "merge_refused"
    #: 检查全绿，但改动超出了人当初授权的范围，平台扣住不合。
    merge_withheld = "merge_withheld"
    #: PR 在 GitHub 上被关掉且没有合并。
    pr_closed_unmerged = "pr_closed_unmerged"
    #: 递卡时开 PR 失败，这张卡还没有 PR。
    pr_open_failed = "pr_open_failed"
    #: 采纳现场补开 PR 失败，采纳已停下。
    accept_pr_open_failed = "accept_pr_open_failed"
    #: 卡带着交付主张，树的分支上却没有任何提交（改动可能在别的分支上），
    #: 采纳已停下。
    accept_no_branch = "accept_no_branch"
    #: 卡上有 PR，但此刻推进不了。
    accept_pr_stalled = "accept_pr_stalled"
    #: 采纳时合并冲突，已派芝士解决。
    merge_conflict = "merge_conflict"
    #: pending_gate 孤儿卡：闸门没跑完，不是没通过。
    gate_abandoned = "gate_abandoned"
    #: 两阶段采纳没走成，这次采纳落回了本地合并。
    pr_skipped = "pr_skipped"
    #: 轮询这张卡时打 GitHub 报错，卡没往前走。
    poll_failed = "poll_failed"

    # —— 还在走，或只是留痕 ——

    #: 等 CI。note 家族里优先级最低的一条：只写进空 note 或它自己。
    waiting_checks = "waiting_checks"
    #: 人工作废。
    voided = "voided"
    #: 人工放行的署名。
    force_merged = "force_merged"
    #: 话题归档时卡被收尾或关闭。
    archived = "archived"


#: 停住了的那一半。新增的码**必须**在这里表态：漏掉就默认 info，也就是和「还在
#: 等检查」长得一模一样——`🌿 分支分叉` 和 `🚪 PR 被关` 都在旧的 emoji 数组里踩过
#: 这个坑，它们红不起来，只是安静地被算成另一类。
_STUCK = frozenset(
    {
        NoteCode.repush_failed,
        NoteCode.repush_diverged,
        NoteCode.poll_paused,
        NoteCode.checks_failed,
        NoteCode.merge_refused,
        NoteCode.merge_withheld,
        NoteCode.pr_closed_unmerged,
        NoteCode.pr_open_failed,
        NoteCode.accept_pr_open_failed,
        NoteCode.accept_no_branch,
        NoteCode.accept_pr_stalled,
        NoteCode.merge_conflict,
        NoteCode.gate_abandoned,
        NoteCode.pr_skipped,
        NoteCode.poll_failed,
    }
)


def note_level(code: NoteCode | None, note: str) -> NoteLevel | None:
    """这条 note 该被画成什么。空 note 返回 None（卡上不显示这一行）。"""
    if not (note or "").strip():
        return None
    if code is not None and code in _STUCK:
        return NoteLevel.error
    return NoteLevel.info


def record(card: "AcceptCard", code: NoteCode | None, text: str) -> None:
    """卡进入一个新状态：文案和码一起落。"""
    card.note = text[:NOTE_MAX]
    card.note_code = code


def annotate(card: "AcceptCard", text: str) -> None:
    """在现有 note 前面加一句，**不动码**。

    给的是补充说明（这次采纳为什么没走 PR、平台自己就是 forge），不是新状态。卡还
    停在原来那件事上，所以判断该看到的仍是原来那个码——这正是过去做不到的事：加一
    句前缀就把所有 `startswith` 挡在了外面。
    """
    old = (card.note or "").strip()
    card.note = (f"{text}；{old}" if old else text)[:NOTE_MAX]


def clear(card: "AcceptCard") -> None:
    """这张卡上没有要说的了。"""
    card.note = ""
    card.note_code = None
