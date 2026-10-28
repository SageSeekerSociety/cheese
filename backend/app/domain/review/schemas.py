"""Accept card request/response schemas (Pydantic v2) — spec §4.4, §6.3."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.review.models import AcceptStatus


class CommentOutcome(BaseModel):
    id: uuid.UUID
    handled: bool
    note: str = Field(default="", max_length=2000)


class AcceptCardCreate(BaseModel):
    # 未指定就用项目默认验收人 (#718 设置表「任务默认 reviewer」). Optional here
    # and resolved in the service — same reason `change_subject` is: the thing
    # that has to reach the filer when nobody is named AND the project has no
    # default is a sentence telling them how to fix it, not pydantic's
    # `Field required`.
    reviewer_handle: str | None = Field(default=None, max_length=64)
    # 一卡多人：递给哪几个人由递卡的人说了算。给了这个就整体替换掉那个集合
    # （去重、去空白）；只给 `reviewer_handle` 时是「一个」。两者都空就爬默认阶梯。
    reviewer_handles: list[str] | None = None
    # 审阅重点，长度在 review/services 里把关：超了要回一句教人怎么写的话，
    # 不是 pydantic 的 `String should have at most N characters`。
    focus: str = ""
    # What the change IS, as one commit title line — becomes the PR title
    # and the squash commit subject. REQUIRED since 2026-08-17, but enforced in
    # review/services.py rather than here: a Pydantic-required field answers
    # 422 with pydantic's own wording, and the thing that has to reach the
    # filer is a sentence teaching them how to write one. Typed optional only
    # so that message — not `Field required` — is what comes back.
    change_subject: str | None = Field(default=None, max_length=255)
    change_body: str | None = None
    # 这次交付动的是清单上哪一项产物 (#1085 结论三): `artifact` 沿用一项，by id —
    # a name would let one typo grow a near-duplicate silently; `new_artifact`
    # declares one the list does not have yet, and that is the call that hands
    # back the new id. Exactly
    # one of them, enforced in review/services.py rather than here for the same
    # reason `change_subject` is: the answer to "you named neither" has to be
    # the sentence that teaches the two actions apart, not pydantic's
    # `Field required`.
    artifact: str | None = None
    new_artifact: str | None = None
    # 这一项产物是什么东西、给谁的 —— 一句话，清单上跟着名字一起显示，下一次交付
    # 靠它判断「我做出来的是不是它的新一版」。新声明一项时必填，沿用一项时可给可
    # 不给（给了就以新的为准）：写对了的那句话说的是这样东西本身，交一版新的不会
    # 让它变得不对。长度和「别抄改动标题」由 deliverables/artifacts.py 的 clean_about
    # 挡，不在这里 —— 同一个理由，那里答得出为什么。
    about: str | None = None
    # 这一版交出去的是什么 (#1085 结论五)：`deliver` 是工作目录里那一份（建卡时落
    # 一份快照，因为构建产物过了这一轮就没了），`deliver_url` 是一个地址。两个都不
    # 给就是交出去这次合并本身，代码仓库这类项目交的就是它。
    deliver: str | None = Field(default=None, max_length=512)
    deliver_url: str | None = Field(default=None, max_length=1024)
    # 这次交付是不是任务的最后一步。不是的话，采纳后任务还开着，接着做下一步。
    completes_task: bool = True
    # 上一次退回附带的批注，芝士逐条说处理了没有（`review/comments.py`）。
    comment_outcomes: list[CommentOutcome] = Field(default_factory=list, max_length=200)


class AcceptCardDescribe(BaseModel):
    """更正一张待处理验收卡的描述（追加 D）。

    没有 `task_ids`，而且不该有：署名是对事实的断言，改它等于往别人头上安署名；
    描述是对这次改动的说明，评审本来就是要求改它的。理由写在
    `AcceptService.redescribe` 里。
    """

    change_subject: str | None = Field(default=None, max_length=255)
    #: None = 不动正文（只改标题）；空串 = 把正文清空。这两件事不一样，所以
    #: 「没传」和「传了空的」必须能区分，默认值才是 None 而不是 ""。
    change_body: str | None = None
    #: 审阅重点：交付的人请审阅人确认的那几件事。None = 不动它；空串 = 清空。
    #: 上限和递卡时同一道检查（`cards.clean_focus`：三条、合计 300 字），理由
    #: 写在 `AcceptService.redescribe` 里。
    focus: str | None = None


class AcceptCardRedeliver(BaseModel):
    """换掉待处理那张卡交出去的那一份：任务目录里的一份文件，或者一个地址。

    只收这两样。别的字段（标题、正文、验收人）各有各的入口，收下一个不起作用的
    字段，读起来跟起了作用一模一样。
    """

    model_config = ConfigDict(extra="forbid")

    deliver: str | None = Field(default=None, max_length=512)
    deliver_url: str | None = Field(default=None, max_length=1024)


class AcceptCardReassign(BaseModel):
    """改验收人：只收新的验收人；不点名就回到项目默认的那一位。

    这条路过去拿递卡的请求体当自己的，`deliver`、`artifact` 之类照收不误、一个都
    不用，调用方以为改了交付物，其实什么都没变 (FB-104)。多给的字段现在整个请求被拒。
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_handle: str | None = Field(default=None, max_length=64)


#: 合的是**人看到的**那个 commit：每个会触发合并的写入口都带上前端渲染这张卡
#: 时卡面显示的 head sha（`merge_state.head_sha`）。它不是给服务端「用哪个 sha
#: 合并」的建议，而是给它「我看的是哪一版」的声明——服务端拿它和卡当前的
#: `pr_head_sha` 对；不一致就是「你看的那版已经不在了」，请求被拒、人重新看过
#: 再点。没有它的话，轮询器在渲染和点击之间把卡刷到新 head，点下去合的就是一
#: 段没有人看过的代码。None 是合法值：卡还没被轮询器镜像过 head（刚递的卡）、
#: 或者根本不骑 PR（平台 lane）时，卡面显示的就是「没有 sha」。
_SEEN_HEAD_FIELD = Field(default=None, max_length=64)


class AcceptDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    head_sha: str | None = _SEEN_HEAD_FIELD
    #: 交文件、交地址的卡：卡面上 `deliverable.revision` 那一版，同 `head_sha`
    #: 一个道理 —— 交付物可以在卡待处理时原地换掉，合的必须是人看过的那一份。
    deliverable_revision: str | None = Field(default=None, max_length=64)


class RejectDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    note: str = ""
    #: The decider's draft comments that go back with this 退回.
    comment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class ReviewCommentIn(BaseModel):
    """A comment on lines of the version under review, or a reply (`parent_id`)."""

    path: str = Field(default="", max_length=1024)
    line_start: int = 0
    line_end: int = 0
    line_text: str = Field(default="", max_length=200_000)
    #: For a Word document, a deck or a workbook: `p3`, `s2` or `汇总!C5`; a
    #: text file's place is its lines and this stays empty.
    place: str = Field(default="", max_length=255)
    commit_sha: str | None = Field(default=None, max_length=64)
    body: str = ""
    suggestion: str | None = None
    parent_id: uuid.UUID | None = None


class ReviewCommentEdit(BaseModel):
    body: str = ""
    suggestion: str | None = None


class VoidDecision(BaseModel):
    """人工作废 (2026-08-11). No `decided_by`, unlike the other decisions here:
    作废 is an authorization action, so the actor comes from the session token
    only — a caller must never be able to name someone else as the one who
    did it. Same shape as 改验收人 (`reassign`)."""

    note: str = Field(default="", max_length=2000)


class ForceMergeDecision(BaseModel):
    """人工放行。No `decided_by`, same reason as 作废: this is an authorization
    action and the signature is the entire point — the actor comes from the
    session token only, never from the body, or "谁明知红仍合并" would be
    whatever the caller typed."""

    reason: str = Field(default="", max_length=2000)
    head_sha: str | None = _SEEN_HEAD_FIELD


class AutoMergeDecision(BaseModel):
    """绿了自动合 (#718) 的开关。Actor 同样只来自 session token：布防等于提前
    采纳，布防人是谁必须由平台认定。"""

    enabled: bool
    head_sha: str | None = _SEEN_HEAD_FIELD


class ApprovalCreate(BaseModel):
    approver_handle: str = Field(min_length=1, max_length=64)


class AcceptCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    task_id: uuid.UUID | None = None
    # 递给谁验收。不在 `accept_cards` 那一行上了（评审人在 `accept_card_reviewer`
    # 表里），所以 `model_validate(card)` 拿不到它 —— `describe` 从表里取出来补上。
    reviewer_handle: str | None = None
    # 一卡多人：完整的集合。`reviewer_handle` 是它的「、」拼接，留着给旧读者。
    reviewer_handles: list[str] = []
    focus: str
    # The commit this card will become, as filed — so the reviewer can see the
    # subject that is about to enter the project's history BEFORE accepting,
    # which is the last moment anyone can object to it.
    change_subject: str | None = None
    change_body: str | None = None
    status: AcceptStatus
    decided_by: str | None
    #: ``decided_by``'s own 芝士 decided for them.
    decided_by_assistant: bool = False
    decided_at: datetime | None
    note: str
    #: 这条 note 是「停住了」(error) 还是「还在走」(info)，空 note 是 None。
    #: 服务端从卡的状态码算好下发 (domain/review/notes.py)，浏览器只把它画成
    #: 颜色，不再去读文案开头那个字符。
    note_level: str | None = None
    created_at: datetime
    # 机器闸门 (eval C2): when the check_command started / passed. `started` is
    # NULL on a card whose gate never actually ran — see models.AcceptCard.
    gate_started_at: datetime | None = None
    gate_passed_at: datetime | None = None
    gate_output: str = ""
    # PR-based accept (#188 §5.1 → #718): the real GitHub PR this card rides
    # on. pr_head_sha is the head the card shows — the commit an accept click
    # will merge; see models.AcceptCard.
    pr_number: int | None = None
    pr_url: str | None = None
    pr_repo: str | None = None
    pr_head_sha: str | None = None
    pr_merged_at: datetime | None = None
    #: 采纳之后任务是完成了，还是接着做下一步。
    completes_task: bool = True
    # 主分支保护 (spec §4.4): votes so far / votes needed. Enriched by the
    # service (approvals live in their own table; the requirement is a project
    # setting), so plain model_validate(card) keeps the defaults.
    approvals: list[str] = []
    approvals_required: int = 1
