"""验收卡 note 的码与级别：判断读的那一半，住在最低一层。

一张卡的 `note` 是给人看的一句话，`note_code` 是给代码看的状态。判断读码、渲染读
码算出的级别，文案怎么写都不影响任何一方（和 `platform_failures.py` 的 code + copy
contract 同一条规矩）。怎么写入这张卡（`record` / `annotate` / `clear`）留在
`review/notes.py`，它要卡的两列。

## 为什么住在第 1 层

`room_task.presentation`（第 4 层）要把一张卡的码摆成看板上的一格，而 `review` 反过
来读 `room_task` —— 两边各自 import 对方就是一个环（C3 的 `domains-acyclic`）。把
**词汇**搬下来、写入留在原地，是这一层里已经做过几次的同一个动作（`notice_meta`、
`presentation_meta`、`delivery_events`）。
"""

import enum

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
    #: 递卡时开 PR 失败，这张卡还没有 PR。
    pr_open_failed = "pr_open_failed"
    #: 采纳现场补开 PR 失败，采纳已停下。
    accept_pr_open_failed = "accept_pr_open_failed"
    #: 卡带着交付主张，树的分支上却没有任何提交（改动可能在别的分支上），
    #: 采纳已停下。
    accept_no_branch = "accept_no_branch"
    #: 卡上有 PR，但此刻推进不了。
    accept_pr_stalled = "accept_pr_stalled"
    #: GitHub 上的 PR 标题和这张卡的主题不是同一行。平台按卡片主题写 PR 标题、
    #: 按同一行落 squash 提交标题，而仓库自己的检查评判的是 PR 上那个标题 ——
    #: 两者分家时合下去，落进历史的会是一行没人检查过的字，所以合并停下。
    pr_title_disagrees = "pr_title_disagrees"
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
    waiting_merge_queue = "waiting_merge_queue"
    #: 人工作废。
    voided = "voided"
    #: 人工放行的署名。
    force_merged = "force_merged"
    #: 话题归档时卡被收尾或关闭。
    archived = "archived"
    #: 卡挂着的活已经关了，审阅随之关闭。
    task_closed = "task_closed"


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
        NoteCode.pr_open_failed,
        NoteCode.accept_pr_open_failed,
        NoteCode.accept_no_branch,
        NoteCode.accept_pr_stalled,
        NoteCode.pr_title_disagrees,
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
