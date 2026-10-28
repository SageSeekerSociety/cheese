"""PR 上发生的事，怎么回来找写这段代码的芝士 —— CI 挂了、有人留了评审意见、
和主分支冲突了。

三件事各自独立，谁都不蕴含谁，所以 `AcceptService._poll_pr_card` 把它们各排一条
待发（`pr_signals.PendingNudge`），由 `_dispatch_nudges` 一次性发完。去重、账本、
「哪些算新事实」的签名在 `pr_signals.py`；这里只有**话** —— 说什么、说到什么程
度、以及哪一种情况不该说。

和 `pr_text.py` 是同一种切法：那边是 topic 留在 GitHub 上的字（PR 标题、正文、
squash 提交信息），这边是 GitHub 上的事回流给芝士的字。

这三段原先是不碰实例状态的方法（只读卡片上的 PR 号/链接/head、洗过的外部字符串、
和一个列行情的 client），所以它们是模块函数，`AcceptService` 上留一行同名委托，
调用点与测试的写法一格没动。唯一改了形状的是 `_review_nudge` 的 `creds` 标注：
它要的是「一把能读的钥匙」，不是 services 里那个两把钥匙的类型 —— 为什么不能把
那个类型引回来，见 `_ReadToken`。
"""

from __future__ import annotations

from typing import Final, Protocol

from app.core.sentences import say
from app.domain.notice_meta import (
    EVENT_CI_FAILED,
    EVENT_CI_PASSED,
    EVENT_MERGE_REFUSED,
    EVENT_PR_CONFLICT,
    EVENT_PR_REVIEW,
    SEVERITY_INFO,
)
from app.domain.review import notes, pr_signals, pr_text
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic


class _ReadToken(Protocol):
    """`_review_nudge` 只用得上 `read` 那一把钥匙 —— 带 `checks:read` 的那把。

    完整的凭据、以及「两把钥匙分不开就会坏」的原委在 `services.py` 的
    `_GitHubCredentials` 上：GET 用 `read`，推分支和合并用 `write`。这里不把那个
    类型引回来 —— `services` 引 `nudges`（兼容门面），`nudges` 再引回 `services`
    就是这个包里的一条环，`.importlinter` 的 domains-acyclic 立刻多一条要冻结的
    边。所以只按结构声明用得到的那一半，和 `api/routes/users_sessions.py` 的
    `_SessionRow` 是同一种写法。
    """

    read: Final[str]


def _nudge_note_prefix(stage: str) -> str:
    return f"{stage} 检查未通过："


#: How much of the failure detail rides in the nudge message. The detail is
#: already bounded per job upstream (`github_pr._failure_detail`); this is the
#: backstop that keeps a pathological payload from flooding the topic.
_NUDGE_TAIL_LIMIT = 4000


def _ci_log_howto(repo_full_name: str) -> str:
    """The "where do I read the rest" paragraph of a CI-failure nudge.

    Name the repository so the native CLI can fetch the complete job log.
    The launcher supplies credentials for this invocation.
    """
    repo = repo_full_name or "<owner>/<repo>"
    return (
        "上面是失败 job 的名字、Actions 页面链接，以及日志里错误行附近的片段。"
        "要看完整日志，在本频道的工作区里跑：\n"
        "```bash\n"
        f"gh api repos/{repo}/actions/jobs/<job_id>/logs\n"
        "```\n"
        "（`<job_id>` 就是上面 Actions 链接里 `/job/` 后面那串数字；"
        f"要重新列出这次提交的所有检查：`gh api "
        f"repos/{repo}/commits/<head_sha>/check-runs`。）\n"
    )


def _ci_nudge(
    *,
    card: AcceptCard,
    tail: str,
    stage: str,
    owner: str,
    repo: str,
) -> pr_signals.PendingNudge | None:
    """「这个 PR 的检查红了」排成一条待发，或者 None（这轮不该说）。

    只剩**一个**理由不说：重推失败 / 分支分叉。两者都意味着芝士的修复根本没到
    GitHub，所以 PR 上那片红是旧的，催它再修一遍是催错了对象
    （docs/topics/诊断信息搬上验收卡.md 的优先级说明）。

    「已经叫过了」不再是这里的判断。它以前是 —— 判据是 `note_code ==
    checks_failed`，也就是拿**卡面状态**当去重键，于是任何别的东西写一次 note
    就能顶掉它（2026-08-10 那次 `⚠️ 轮询暂停` 吞掉全部 CI 失败，就是这条的极端
    形态）。现在去重按内容走账本（`_dispatch_nudges`），卡面爱怎么写怎么写。
    """
    # CI 日志是仓库外的人能控制的字符串（谁都能提个 PR 让 workflow 打印任意
    # 字节），而它最终会被贴进芝士的终端。控制字符在这里就洗掉。
    clean = pr_signals.sanitize_external(tail)
    # `tail` is a headline PLUS per-job links and log excerpts (see
    # `github_pr._failure_detail`). The card's note is a one-line field in
    # the UI, so only the headline goes there — the detail is exactly what
    # the message is for, and duplicating it into a 2000-char column would
    # cost the note its glanceability for no reader's benefit.
    headline = clean.splitlines()[0] if clean else ""
    return pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.ci,
        # 签名取 commit + headline，**不取整段 tail**：headline 正是
        # `_summarize_runs` 拼出来的「哪几个 job 挂了」，多挂一个、换一个都会
        # 变；而 tail 里还有日志片段，同一批失败重读一次就可能微妙地不一样，
        # 拿它当签名等于每轮都重发。带上 commit，是因为芝士推了新提交之后同样
        # 的失败是**新事实**，必须再说一次。
        signature=pr_signals.signature(card.pr_head_sha or "", headline),
        event=say("ciFailed", pr=card.pr_number, stage=stage),
        content=(
            f"PR #{card.pr_number}（{card.pr_url}）的{stage}检查没通过：\n"
            f"```\n{clean[:_NUDGE_TAIL_LIMIT]}\n```\n"
            f"{_ci_log_howto(f'{owner}/{repo}')}"
            "先根据检查结论和日志判断原因；代码问题才在对应任务目录修复、验证并提交，"
            "用 cheese push-fix 更新原 PR。取消或等待授权的检查先处理其运行状态。"
            "说明改动和验证结果；采纳由人决定，只有人已启用自动合并且"
            "项目条件满足时才会自动合并。"
        ),
        # 平台提示统一契约: the room sees one line and the excerpt rides in
        # `meta.detail`, under the same `_NUDGE_TAIL_LIMIT` bound the message
        # body always used.
        detail=clean[:_NUDGE_TAIL_LIMIT],
        detail_label=say("labelCiLog", stage=stage),
        note=f"{_nudge_note_prefix(stage)}{headline}",
        note_code=notes.NoteCode.checks_failed,
        event_type=EVENT_CI_FAILED,
    )


def _ci_passed_nudge(*, card: AcceptCard) -> pr_signals.PendingNudge | None:
    """「这个 PR 的检查全绿了」排成一条待发，按 head 去重。

    以前只有红了才回来找芝士，绿了只告诉验收人（`accept_ready`，而且要等评审等
    合并规则都满足）。芝士于是自己 `sleep N; gh pr checks` 一遍遍轮询，一轮活被
    「等 CI」撑到几十分钟（2026-10 的 trace：这类轮询占了长轮里两成的墙钟）。这
    一条把「绿了」也送回去，它就可以交代完别的、停下来等这句话。

    不写卡面：卡面说的是「这张卡停在哪、要谁动手」，检查通过不要任何人动手。
    """
    if card.pr_number is None:
        return None
    return pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.ci_passed,
        signature=pr_signals.signature("passed", card.pr_head_sha or ""),
        event=say("ciPassed", pr=card.pr_number),
        content=(
            f"PR #{card.pr_number}（{card.pr_url}）当前提交上的检查全部通过了。"
            "不用再轮询 CI；采纳由人决定，只有人已启用自动合并且项目条件满足时"
            "才会自动合并。"
        ),
        event_type=EVENT_CI_PASSED,
        severity=SEVERITY_INFO,
    )


async def _review_nudge(
    *,
    card: AcceptCard,
    owner: str,
    repo: str,
    creds: _ReadToken,
    client,
    status,
) -> pr_signals.PendingNudge | None:
    """「有人在 PR 上说话了」排成一条待发。

    去重键是这些意见的 **id 集合**：又来一条新意见必然换签名，同一批被轮询读
    到十次必然不换。GitHub 的 REST v3 说不出一条评论「解决了没有」（那是
    GraphQL 的 review thread 才有的字段），所以这里不假装知道 —— 一条意见叫
    过一次就算说到了，人再说一句就是新的 id、就再叫一次。

    `REVIEW_NUDGE_LIMIT` 到顶之后只写卡面、不再叫芝士：见那个常量的说明。

    列 review 一定要问一次 GitHub；列行内评论只在 PR 自己报了有评论时才问 ——
    绝大多数轮次那个数是 0，省下来的就是每张在飞的卡每分钟一次请求。
    """
    if card.pr_number is None:
        return None
    signals = await client.review_signals(
        owner=owner,
        repo=repo,
        number=card.pr_number,
        token=creds.read,
        with_comments=getattr(status, "review_comment_count", 0) > 0,
    )
    # A 退回's comments, mirrored onto the PR, went to 芝士 with the 退回.
    signals = [
        s for s in signals if pr_signals.PLATFORM_REVIEW_MARK not in (s.body or "")
    ]
    if not signals:
        return None
    ledger = pr_signals.NudgeLedger.load(card.nudge_state)
    signature = pr_signals.signature(*sorted(s.id for s in signals))
    capped = ledger.rounds(
        pr_signals.NudgeKind.review
    ) >= pr_signals.REVIEW_NUDGE_LIMIT and not ledger.already_sent(
        pr_signals.NudgeKind.review, signature
    )
    body = "\n".join(s.line() for s in signals)
    asked = sum(1 for s in signals if s.kind == "changes_requested")
    head = say("prReviewChangesRequested") if asked else say("prReviewCommented")
    if capped:
        return pr_signals.PendingNudge(
            kind=pr_signals.NudgeKind.review,
            signature=signature,
            event=say(
                "prReviewCapped",
                pr=card.pr_number,
                limit=pr_signals.REVIEW_NUDGE_LIMIT,
            ),
            content="",
            note=(
                f"评审意见已自动回流 {pr_signals.REVIEW_NUDGE_LIMIT} 轮仍未收敛，"
                "需要人来看一眼"
            ),
            note_code=notes.NoteCode.accept_pr_stalled,
            capped=True,
        )
    return pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.review,
        signature=signature,
        event=say("prReview", pr=card.pr_number, head=head),
        content=(
            f"{head}（PR #{card.pr_number}，{card.pr_url}）：\n"
            f"{body}\n\n"
            "在对应任务目录按意见修改、验证并提交，用 cheese push-fix 更新原 PR。"
            "如果你不同意某条意见，"
            "在频道里说清理由，让人来定。"
        ),
        detail=body,
        detail_label=say("labelReviewComments"),
        note=f"{head}（{len(signals)} 条）",
        note_code=notes.NoteCode.accept_pr_stalled,
        event_type=EVENT_PR_REVIEW,
    )


def _conflict_nudge(*, card: AcceptCard, status) -> pr_signals.PendingNudge | None:
    """「这个 PR 和目标分支冲突了」排成一条待发。

    判据是 GitHub 的 `mergeable is False` —— **不是** falsy。它在 GitHub 还没
    算完的时候是 None，而刚推完一次的 PR 每次都会经过那个 None：把 None 当冲
    突，等于每次推送都报一次假冲突。

    没有上限。冲突和 CI 失败一样是客观的：解掉它就消失，所以多叫几轮不会白叫
    （评审意见不是，见 `REVIEW_NUDGE_LIMIT`）。

    A dependent task can target its parent's branch; use the PR's actual base.
    """
    if getattr(status, "mergeable", None) is not False:
        return None
    # 分支名是 provider 可控的字符串，而它要被贴进芝士的终端。
    branch = pr_signals.sanitize_external(getattr(status, "head_ref", "") or "")
    where = f"分支 {branch} " if branch else ""
    return pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.conflict,
        # commit 变了就重新算一次：芝士推了一次合并上来，冲突还在，那是新事实。
        signature=pr_signals.signature("conflict", card.pr_head_sha or ""),
        event=say("prConflict", pr=card.pr_number),
        content=(
            f"PR #{card.pr_number}（{card.pr_url}）的{where}和目标分支冲突了，"
            "代码托管服务现在合不了它。\n"
            "先确认 PR 当前目标分支，在对应任务目录合入该分支、解决冲突并验证。"
            "撞的是 .docx、.pptx、.xlsx 这类文件时不要合并内容：git 不在这类文件里"
            "留冲突标记，直接提交会把对方的修改悄悄丢掉。在对方那一版上把你的改动"
            "重做一遍，做法见 cheese 技能里「合并冲突」一节。"
            "提交后用 cheese push-fix 更新原 PR，说明处理结果。\n"
            "如果冲突解不动、或者不该由你来解，在频道里说清楚卡在哪。"
        ),
        note=f"PR #{card.pr_number} 与目标分支冲突，正在解决",
        note_code=notes.NoteCode.merge_conflict,
        event_type=EVENT_PR_CONFLICT,
    )


def _title_nudge(
    *, card: AcceptCard, topic: Topic, status
) -> pr_signals.PendingNudge | None:
    """「PR 上的标题和这张卡不是同一行」排成一条待发，或者 None（一致）。

    一个字符串三个读者：平台按卡片的 subject 写 PR 标题、按同一行落 squash 提交
    标题，而**仓库自己的检查评判的是 PR 上那个标题**（编辑 PR 会重跑它的
    `pull_request` 工作流）。两行一旦分家，平台再合下去，落进历史的是一行仓库从
    没检查过的字 —— 所以这里不是「提示一下」，是两条合并路径都要停下的理由
    （`pr_text.pr_title_matches_subject`）。

    平台不判断哪一行才对：标题该怎么写是仓库自己的规矩（它的 CI 说了算），平台
    只看这两个读者是不是在读同一个字符串。修法只有一条，而且必须从**卡**这边
    改：`cheese_card(action=describe)` 会把 PR 标题一起改掉，仓库的检查会按新标题重跑。反过来
    在 GitHub 上直接改 PR 标题绕不过去 —— 卡片仍是落地的唯一源头。

    去重按内容：签名取「卡片 subject + PR 当前标题」。谁变了都是芝士没见过的新
    事实，都要再说一次（和 CI 失败带上 commit 是同一个理由）。
    """
    subject = pr_text.change_subject(card, topic)
    # 比对用**原样**的标题，和两道合并闸（`merge_queue` 的点击合、`decisions` 的
    # 放行合）读的是同一个字符串。这里先洗再比的话，一行只差几个不可见字节的标题
    # 在轮询这条路上会判成「一致」—— 布防的自动合照合，而点击那条路上同一个 PR
    # 会被拒：同一个事实两条车道两种结论，落进历史的那一行也就没个准。
    raw = (getattr(status, "title", "") or "").strip()
    # 展示用洗过的那一份：两个都是外部可控、会被贴进终端或提交历史的字符串。
    live = pr_signals.sanitize_external(raw).strip()
    if pr_text.pr_title_matches_subject(card, topic, raw):
        return None
    shown = live or "（此刻读不出来）"
    if raw != live:
        # 差别只在不可见字节上，两行看上去一模一样。不点破的话读的人只会以为
        # 平台在无理取闹 —— 仓库的检查评判的是 PR 上原样的那一串字节。
        shown += "（含不可见字符）"
    return pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.pr_title,
        signature=pr_signals.signature("pr-title", subject, raw),
        event=say("prTitleDisagrees", pr=card.pr_number),
        content=(
            f"PR #{card.pr_number}（{card.pr_url}）在 GitHub 上的标题和这张卡不是"
            f"同一行：\n"
            f"- PR 标题：{shown}\n"
            f"- 卡片标题：{subject}\n"
            "平台按卡片标题落 squash 提交标题，而仓库自己的检查评判的是 PR 上那个"
            "标题 —— 现在合下去，写进历史的就是一行没人检查过的字，所以平台停下"
            "不合。\n"
            "标题该怎么写是仓库自己的规矩，平台不猜哪一行才对，也不会替你把 PR "
            "标题改回来。要合并，就用 cheese_card(action=describe) 把这张卡的标题改成该落的那"
            "一行（`subject` 参数）：平台会同时把 GitHub 上的 PR 标题改成它，仓库"
            "的检查会按新标题重跑。改完等检查绿了，再采纳或等自动合并。\n"
            "如果改的是 PR 标题而不是卡片，请在频道里说清为什么卡片那行才是对的。"
        ),
        # 平台提示统一契约：房间那条可见的一行是 `event`，两行标题（都来自外部、
        # 都可能很长）折进 `meta.detail`，谁看都能一眼对上。
        detail=f"PR 标题：{shown}\n提交审阅时的标题：{subject}",
        detail_label=say("labelReason"),
        note=(
            f"PR #{card.pr_number} 在 GitHub 上的标题和提交审阅时的标题不一致，合并已暂停；"
            "两个标题改成同一行、检查通过后就能合并"
        ),
        note_code=notes.NoteCode.pr_title_disagrees,
        # 复用 `merge_refused`：标题分家的后果正是「这次不能这样合并」，语义够用，
        # 也不需要为一个新事件码重建 `CHECKS_FOR_THE_AGENT` 那个部分索引（那会牵出
        # 一份 alembic 迁移）。文案在 `prTitleDisagrees` / `note` 上区分。
        event_type=EVENT_MERGE_REFUSED,
    )
