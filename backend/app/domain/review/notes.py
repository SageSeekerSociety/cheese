"""验收卡 note 的词汇表，和「这条 note 有多严重」。

一张卡的 `note` 是一列自由文本，同时干着两件事：给人看的一句话，和给代码看的
状态。代码那一半过去只能靠**前缀**认出来，而前缀是以 emoji 开头的字符串——于是
「这条 note 意味着什么」这个判断散落在两处不同的语言里：服务端 `startswith(前缀
常量)`，浏览器端一份硬编码的 emoji 数组。

那份数组漏过东西：`🌿 本地分支与 PR 分支已分叉` 是 2026-08 加的，卡在这个状态上
是**停住了、等人动手**，而浏览器那边的列表只认 `⚠️ ❌ 🚫 ✋`，于是它和「还在等
检查」渲染成同一个颜色。这就是把分类交给"读文案开头那个字符"的代价：新增一条
前缀不会让任何东西变红，它只是安静地被算成另一类。

所以分级住在这里——**和前缀常量本身住在一起**，服务端算完随卡下发一个码，浏览器
只负责把码画成颜色。和 `platform_failures.py` 的 code + copy contract 同一条规矩：
文案在前端，判断在后端，中间传的是码不是字符。
"""

import enum


class NoteLevel(enum.StrEnum):
    """这条 note 说的是「停住了」还是「还在走」。"""

    #: 需要人或芝士动手，卡不会自己往前走。
    error = "error"
    #: 平台还在推进，或者只是一条留痕。
    info = "info"


#: 平台自动重推失败：芝士的修复没能到 GitHub，PR 上的红是旧的。
REPUSH_FAILED_PREFIX = "⚠️ 平台自动重推失败"
#: 本地分支与 PR 分支已分叉，平台不替它强推覆盖 PR 上的提交。
REPUSH_DIVERGED_PREFIX = "🌿 本地分支与 PR 分支已分叉"
#: 轮询用的 token 失效，卡不再自己推进。
POLL_PAUSED_PREFIX = "⚠️ 轮询暂停"
#: 采纳现场补开 PR 失败。
ACCEPT_PR_OPEN_FAILED_PREFIX = "⚠️ 采纳未完成：无法为这张卡开 PR"
#: 卡上有 PR 但此刻推进不了。
ACCEPT_PR_STALLED_PREFIX = "⚠️ 采纳未完成：PR 未能合并"
#: 合并很久了，部署既没成功也没失败。比「还在等」强、比「部署失败」弱。
DEPLOY_STALLED_PREFIX = "⏳ 部署迟迟没有完成"
#: pending_gate 孤儿卡：闸门没跑完，不是没通过。
GATE_ABANDONED_PREFIX = "⏱ 闸门没跑完"
#: 人工作废，谁作废的、为什么。
VOIDED_PREFIX = "🗑 卡片已作废"
#: 等 CI。note 家族里优先级最低的一条。
WAITING_CHECKS_PREFIX = "⏳ 等 CI"
#: 人工放行的署名。
FORCE_MERGED_PREFIX = "🔨 人工放行"

#: 每一次自动 rebase 在 note 里留一个的记号，用来给 rebase 轮数封顶。
REBASE_NOTE_MARK = "⟲"

#: 前缀 → 这条 note 有多严重。**新增前缀必须进这张表**：漏掉的那条会被算成
#: `info`，也就是和「还在等检查」长得一模一样——正是 `🌿` 踩过的那个坑。
_LEVELS: dict[str, NoteLevel] = {
    REPUSH_FAILED_PREFIX: NoteLevel.error,
    REPUSH_DIVERGED_PREFIX: NoteLevel.error,
    POLL_PAUSED_PREFIX: NoteLevel.error,
    ACCEPT_PR_OPEN_FAILED_PREFIX: NoteLevel.error,
    ACCEPT_PR_STALLED_PREFIX: NoteLevel.error,
    GATE_ABANDONED_PREFIX: NoteLevel.error,
    DEPLOY_STALLED_PREFIX: NoteLevel.info,
    VOIDED_PREFIX: NoteLevel.info,
    WAITING_CHECKS_PREFIX: NoteLevel.info,
    FORCE_MERGED_PREFIX: NoteLevel.info,
}

#: 还没有具名前缀、但确实表示「停住了」的开头。这些是 note 里直接拼进去的
#: 单字符标记（CI 没过 / GitHub 拒绝合并 / 命中安全阀），列在这里是为了让
#: 分级完整；把它们提升成具名前缀是下一步，不是这一步。
_BARE_ERROR_MARKS = ("❌", "🚫", "✋", "⚠️")


def note_level(note: str) -> NoteLevel | None:
    """这条 note 该被画成什么。空 note 返回 None（卡上不显示这一行）。"""
    text = (note or "").strip()
    if not text:
        return None
    for prefix, level in _LEVELS.items():
        if text.startswith(prefix):
            return level
    if text.startswith(_BARE_ERROR_MARKS):
        return NoteLevel.error
    return NoteLevel.info
