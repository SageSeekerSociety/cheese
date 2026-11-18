"""新会话开场时摆在它面前的那份现状。

一个会话自己查不到、又必须知道的东西：这是哪条支线、频道里有哪些话题、成员表、
项目目标、实况文档、记忆索引、教学安排。它们拼成的是新会话第一条消息前面的开场
快照（:class:`SessionOpening`）；会话活着的时候只把变了的那几段再说一次
（:func:`opening_changes`）。系统提示词那一边一个字都不带现状（`render.render_system`）。

**这里成段的是事实，不是规矩。** 规矩一个会话里一字不变，这里每一条都可能中途变，
所以每段单独存着、单独比对。实况文档的注入预算和丢弃顺序也在这里：那是一份事实
太长时唯一的处置办法。

这一层只用标准库（文档站的生成器在没有后端依赖的机器上跑它），所以记忆索引、题目
阶段、教学安排都按它们读得到的样子（`Protocol`）收进来，不导入它们的类。
"""

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.domain.prompts.memory import memory_block
from app.domain.prompts.notices import NoticeType, platform_prompt
from app.domain.prompts.text import piece


class IndexSectionLike(Protocol):
    label: str
    prefix: str
    text: str


class MemoryIndexLike(Protocol):
    @property
    def sections(self) -> Sequence[IndexSectionLike]: ...

    @property
    def warnings(self) -> Sequence[str]: ...

    def is_empty(self) -> bool: ...


class StageLike(Protocol):
    index: int
    name: str
    description: str
    due_at: datetime | None
    status: str


class TeachingLike(Protocol):
    mode: str | None
    system_prompt: str | None
    space_prompt: str | None
    learning_goals: list[str]
    private_prompt: str | None
    skills: list[str]

    @property
    def is_empty(self) -> bool: ...

    @property
    def ladder(self) -> bool: ...

    def fill(self, template: str) -> str: ...


class TeachingContextLike(Protocol):
    course: str | None
    teaching: TeachingLike
    materials: list[dict]
    documents: list[dict]


# How a roster row is in the project, in the words the agent reads. An external
# member is someone from outside the team taking part in this one project.
_STANDING = {"owner": "项目所有者", "team": "团队成员", "external": "外部成员"}


def _standing(member: dict, names: dict[str, str]) -> str:
    """An agent a member brought says whose it is, by the name the roster gives
    that person: what the agent says is its own, and without this line a reader
    of the transcript cannot tell it from what its owner said."""
    if member.get("agent"):
        owner = member.get("owner_handle")
        if owner is None:
            return "AI 队友"
        if owner in names:
            return f"AI 队友，{names[owner]}带来的"
        return "AI 队友，带它来的人不在这张表上"
    return _STANDING.get(member.get("source") or "", "成员")


#: The ways of helping a course picks from (`protocol.MODES`); each says what
#: it asks of the AI teammate in `pieces/opening/mode_<mode>.md`.
_MODES = ("guide", "example", "spec", "free")


def default_teaching_prompt() -> str:
    """教育性题目的默认教学要求（#944）：`learning_goals` 说了「这一节要学」而老师
    没写自己的 `system_prompt` 时，这段话顶上。与前端
    `spaces.teaching.defaultTemplate`（点「用这段默认要求」填进框里的那份）是同一段
    话的两份副本：那边是给人看、给人改的字面，这边是没人填时的兜底；改动两处要
    同步。"""
    return piece("opening/default_teaching")


#: How a stage stands, in the words the task page shows it by
#: (`submission_state` STAGE_*).
_STAGE_SAYS = {
    "NOT_SUBMITTED": "未提交",
    "PENDING_REVIEW": "待批改",
    "PASSED": "已通过",
    "REJECTED": "被退回",
    "MISSED": "未通过（过了截止没有提交）",
}


def stages_section(stages: Sequence[StageLike]) -> str | None:
    """这个项目所出自的题目分成的阶段：每个阶段的名称、截止时间、要求，以及这个
    项目在每个阶段上走到哪一步。题目不分阶段时是 None，一个字都不加。

    它会变（交了一版、批改了、出题人改了截止），所以是一段跟踪着再说一次的现状
    （``TRACKED_SECTIONS``）。"""
    if not stages:
        return None
    lines = []
    for stage in stages:
        due = (
            f"截止 {stage.due_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
            if stage.due_at is not None
            else "不设截止"
        )
        lines.append(
            f"{stage.index + 1}. {stage.name}（{due}）："
            + _STAGE_SAYS.get(stage.status, stage.status)
        )
        if stage.description:
            lines.append("   " + stage.description.replace("\n", "\n   "))
    return piece("opening/stages", lines="\n".join(lines))


def teaching_section(context: TeachingContextLike) -> str | None:
    """这门课希望 AI 队友怎么帮学生 —— 课程那一段，或者 None。

    **None 是默认，而且是字面意义上的「一个字都不加」。** 一个不是课程的项目
    没有这份设置，说了会让 agent 以为自己在一门课里。

    空间的要求和这道题的要求都生效，分开写：agent 读得出哪句是整门课的、哪句是这道
    题加的。
    """
    teaching = context.teaching
    if teaching.is_empty:
        return None
    parts = ["## 辅导学生" + (f"（课程：{context.course}）" if context.course else "")]
    if teaching.mode in _MODES:
        parts.append(piece(f"opening/mode_{teaching.mode}"))
    if teaching.ladder:
        # 引导为主且打开了提示阶梯：四级逐级给，每条回复标出第几级
        # （`app.domain.task.guide` 读这个标记、记下、从消息里去掉）。
        parts.append(piece("opening/hint_ladder"))
    if teaching.space_prompt and teaching.system_prompt:
        parts.append(
            piece("opening/teaching_space", text=teaching.fill(teaching.space_prompt))
        )
        parts.append(
            piece("opening/teaching_task", text=teaching.fill(teaching.system_prompt))
        )
    elif teaching.system_prompt or teaching.space_prompt:
        # Only one level wrote requirements — a 题目 that says something else of
        # its own (a way of helping, skills) still has the 空间's in force.
        parts.append(
            teaching.fill(teaching.system_prompt or teaching.space_prompt or "")
        )
    elif teaching.learning_goals:
        # 教育性（写了学习目标）而老师没留自己的要求：带上默认教学要求 ——
        # 「留空」在这里是「用你的教学规矩」，不是「没规矩」。只有范围没有
        # 目标的配置（纯课件/周次）不触发。
        parts.append(default_teaching_prompt())
    if teaching.learning_goals:
        # 靶子单列一行，不揉进要求里：验收和展示（`cheese_show`）都指着它。
        parts.append(
            piece(
                "opening/teaching_goals",
                lines="\n".join(f"- {goal}" for goal in teaching.learning_goals),
            )
        )
    if teaching.private_prompt:
        # 出题人的私有指导：**照它做，但不原样透露给学生**。用 prompt 说，不新造
        # 权限开关（见 `Teaching.private_prompt`）。
        parts.append(
            piece(
                "opening/teaching_private", text=teaching.fill(teaching.private_prompt)
            )
        )
    if context.materials:
        parts.append(
            piece(
                "opening/teaching_materials",
                lines="\n".join(
                    f"- 《{m['name']}》{m['url']}" for m in context.materials
                ),
            )
        )
    if context.documents:
        # 只给标题，正文按 id 读：一份文档可以有多长，不是这门课说了算的。
        parts.append(
            piece(
                "opening/teaching_documents",
                lines="\n".join(
                    f"- 《{d['title']}》（id={d['id']}）" for d in context.documents
                ),
            )
        )
    if len(parts) == 1 and teaching.skills:
        # 只配了技能的题目：技能正文走技能那一段（`carried_skills`），这一段没有
        # 话要说。
        return None
    return "\n\n".join(parts)


#: 实况文档的注入预算（#1535 第 1 条）。无上限的文档注入是实测到 79K 字符失控的
#: 那个洞；帽子按块各戴各的，超了按丢弃顺序压缩，全文改走按需读取。
OVERVIEW_DOC_CHAR_BUDGET = 6000
TOPIC_DOC_CHAR_BUDGET = 6000

_DOC_HEADING_RE = re.compile(r"(?m)^#{1,6} .*$")
_TEMP_SECTION_RE = re.compile(r"临时|TODO|待办|草稿|暂定|scratch|todo", re.IGNORECASE)
_PROGRESS_SECTION_RE = re.compile(r"进展|进度|状态|日志|记录|下一步|本周|历史")


def _doc_drop_class(label: str) -> int:
    """0 = 先丢（临时/待办），1 = 次之（进展/记录），2 = 最后才动。"""
    if _TEMP_SECTION_RE.search(label):
        return 0
    if _PROGRESS_SECTION_RE.search(label):
        return 1
    return 2


def fit_doc_to_budget(text: str, budget: int, *, full_read_hint: str) -> str:
    """压一份实况文档进预算帽；没超帽就原样返回，一个字节都不动。

    超帽按小节整段丢，丢弃顺序写死（先丢什么是一份契约，不是启发式）：临时/待办
    先丢，进展记录次之，目标/约束/决策/约定最后才动；全丢完仍超帽，剩下的从尾部
    硬截。压缩过才附一行说明——原文多长、全文去哪读；静默截断读起来就是「全文就
    这么长」。纯函数：提示词每轮重写，同样的输入必须永远得到同样的输出。
    """
    if len(text) <= budget:
        return text
    original = len(text)
    headings = list(_DOC_HEADING_RE.finditer(text))
    segments: list[tuple[int, str]] = []
    if headings:
        preamble = text[: headings[0].start()].rstrip("\n")
        if preamble:
            # 标题前的无题开头按保留处理：它多半是目标/背景，宁可硬截也不先丢。
            segments.append((2, preamble))
        for i, match in enumerate(headings):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[match.start() : end].rstrip("\n")
            segments.append((_doc_drop_class(match.group(0)), body))
    else:
        # 无题长文按段落切，段落的第一行就是它的判据。
        for para in (p for p in text.split("\n\n") if p.strip()):
            segments.append((_doc_drop_class(para.splitlines()[0]), para.strip("\n")))

    def joined(segs: list[tuple[int, str]]) -> str:
        return "\n\n".join(body for _, body in segs)

    kept = segments
    for drop_class in (0, 1):
        i = 0
        while len(joined(kept)) > budget and i < len(kept):
            if kept[i][0] == drop_class:
                del kept[i]
            else:
                i += 1
    body = joined(kept)
    if len(body) > budget:
        body = body[:budget]
    return body + piece(
        "opening/doc_budget_note",
        original=original,
        kept=len(body),
        hint=full_read_hint,
    )


#: 开场快照里，会话期间变了要再告诉一次的那几段。实况文档不在里面：它被人改过时
#: 平台已经发一条「请重读」的提醒（`block/documents.py`）。教学配置也不在：一个会话
#: 有意保持开场那一份到下一次新会话。运行环境只在开场时有意义。题目那一段在里面：
#: 出题人改了题目，正在做的会话下一轮就要知道到了第几版、改了什么。
TRACKED_SECTIONS = (
    "tasks",
    "topics",
    "artifacts",
    "roster",
    "overview",
    "memory",
    "task",
    "stages",
    "package",
)


def task_wrap_up(*, has_checkout: bool, coordinated: bool = False) -> str:
    """一件任务怎么收尾，按事情的性质。``has_checkout``：任务有自己的工作目录（项目
    有代码仓库）；没有的，不提工作目录和合并。``coordinated``：任务是频道主线开的，
    做没做完由主线判断，任务里的 AI 队友不关闭它。"""
    return piece(
        "opening/task_wrap_up_coordinated" if coordinated else "opening/task_wrap_up",
        edit=piece("opening/task_wrap_up_edit") if has_checkout else "",
    )


def task_section(
    *,
    owner: str | None,
    contributors: Sequence[str] = (),
    closed: bool = False,
    has_checkout: bool,
    coordinated: bool = False,
) -> str:
    """任务会话那一段：谁的话是指令，在哪里做、怎么收尾。``coordinated``：任务是
    频道主线开的（`task_wrap_up`）。

    它会变：支线转为任务时，会话接着原来的那段对话，开场不会再发一遍；负责人换人、
    协作者进出、任务关闭和重新打开时也一样。所以它是一段会再告诉一次的现状
    （``TRACKED_SECTIONS``），不写死在开场里。"""
    if closed:
        return piece("opening/task_closed")
    people = [f"@{h}" for h in (owner, *contributors) if h]
    who = (
        piece(
            "opening/task_who",
            owner=people[0],
            contributors=f"，协作者是 {'、'.join(people[1:])}" if people[1:] else "",
        )
        if people
        else ""
    )
    return piece(
        "opening/task_open",
        who=who,
        wrap_up=task_wrap_up(has_checkout=has_checkout, coordinated=coordinated),
    )


def became_task_prompt(*, owner: str | None, has_checkout: bool) -> str:
    """支线转为任务那一刻交给 AI 队友的话：人点「转为任务」时是一条指令，叫它马上
    开工；它自己转的（`cheese_task(action=open)` 的 ``here``）在工具的回复里读到同样的话。
    怎么收尾不在这里说：每一轮的「这个任务」那一段（`task_section`）说。"""
    return piece(
        "opening/became_task",
        who=f"负责人是 @{owner}。" if owner else "",
        start=piece(
            "opening/became_task_checkout"
            if has_checkout
            else "opening/became_task_plain"
        ),
    )


@dataclass(frozen=True)
class SessionOpening:
    """一个新会话开场时项目的样子，按段存着，好在之后比对哪一段变了。"""

    sections: dict[str, str]

    @property
    def text(self) -> str:
        if not self.sections:
            return ""
        return platform_prompt(
            piece("opening/situation", sections="\n\n".join(self.sections.values())),
            NoticeType.state,
        )

    def digests(self) -> dict[str, str]:
        return {
            key: hashlib.sha256(self.sections[key].encode()).hexdigest()
            for key in TRACKED_SECTIONS
            if key in self.sections
        }


def opening_changes(opening: SessionOpening, told: dict[str, str] | None) -> str:
    """一条接着跑的对话在这一轮要补听的现状；什么都没变就是空字符串。

    ``told`` 是上次告诉它时每一段的摘要（:meth:`SessionOpening.digests`），只补
    和它不一样的那几段。没有记录（``None``）时整份都说：那是一条在系统提示词还
    带着现状时开的老对话，或者记录丢了，或者这条对话刚压缩过上下文（压缩把开场
    快照所在的那段历史换成了摘要，平台听到压缩完成就把记录清掉，
    `room/reads.py`），它手上的现状不知道是哪一刻的。一条新开的对话用不上这一段，
    它的第一条消息带着整份快照（``Opening.session_opening``）。"""
    if told is None:
        return opening.text
    changed = [
        text
        for key, text in opening.sections.items()
        if key in TRACKED_SECTIONS
        and told.get(key) != hashlib.sha256(text.encode()).hexdigest()
    ]
    if not changed:
        return ""
    return platform_prompt(
        piece("opening/changes", sections="\n\n".join(changed)), NoticeType.state
    )


def build_session_opening(
    *,
    thread: str | None = None,
    channel: str | None = None,
    tasks: str | None = None,
    doc: str | None = None,
    memory: MemoryIndexLike | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    overview_doc: str | None = None,
    overview_doc_id: uuid.UUID | None = None,
    environment: list[str] | None = None,
    teaching: TeachingContextLike | None = None,
    stages: Sequence[StageLike] = (),
    keeps_memory: bool = False,
    task: str | None = None,
    package: str | None = None,
) -> SessionOpening:
    """新会话第一条消息前面的那份现状：支线、频道、产物、成员、项目目标、文档、记忆索引。

    ``task``：任务会话那一段（:func:`task_section`）；别处是 None。
    ``package``：从题目来的项目里「题目」那一段（``task.package.section_for_project``）：
    第几版、材料在资料库的 `题目/`、这一版改了什么；别的项目是 None。
    ``thread``：支线里的会话那一段（``thread_context``）；``channel``：频道主线上
    的会话那一段（``channel_context``）；别处都是 None。
    ``tasks``：频道主线或支线所在频道还在进行的任务（``channel_tasks``）；别处是
    None。它会变，所以和上面那一段分开、跟踪着再说一次。"""
    sections: dict[str, str] = {}
    if thread:
        sections["thread"] = piece("opening/thread", text=thread)
    if channel:
        sections["channel"] = piece("opening/channel", text=channel)
    if tasks:
        sections["tasks"] = tasks
    if task:
        sections["task"] = task
    if teaching is not None and (section := teaching_section(teaching)):
        sections["teaching"] = section
    if section := stages_section(stages):
        sections["stages"] = section
    if package:
        sections["package"] = package
    if topics:
        sections["topics"] = piece(
            "opening/topics", lines="\n".join(f"- {t['title']}" for t in topics)
        )
    if artifacts is not None:
        # 产物清单进开场，是为了让下一次交付点得准名字。怎么点名、about 怎么写，
        # 规则在 `cheese_card(action=request)` 的工具说明里（与代码同源），这里只给清单
        # 和一句指路。
        if artifacts:
            sections["artifacts"] = piece(
                "opening/artifacts",
                lines="\n".join(
                    f"- 《{a['name']}》"
                    + (f"　第 {a['version']} 版" if a["version"] else "　还没交付过")
                    + (f"　{a['about']}" if a.get("about") else "")
                    + f"　id={a['id']}"
                    for a in artifacts
                ),
            )
        else:
            sections["artifacts"] = piece("opening/artifacts_empty")
    if roster:
        names = {m["handle"]: m["name"] for m in roster if not m.get("agent")}
        sections["roster"] = piece(
            "opening/roster",
            zones=piece("opening/roster_zones")
            if any(m.get("timezone") for m in roster)
            else "",
            lines="\n".join(
                f"- {m['name']}（{_standing(m, names)}，handle: {m['handle']}"
                + (f"，{m['about']}" if m.get("about") else "")
                + (f"，时区 {m['timezone']}" if m.get("timezone") else "")
                + "）"
                for m in roster
            ),
        )
    if overview_doc:
        # 人和 agent 共同看的东西是文档，不是一个共享记忆池（结论 7）：每个项目
        # 有一份项目目标，每段对话都读到同一份，谁改了都留痕。
        where = f"（`document: {overview_doc_id}`）" if overview_doc_id else ""
        sections["overview"] = piece(
            "opening/overview",
            where=where,
            doc=fit_doc_to_budget(
                overview_doc,
                OVERVIEW_DOC_CHAR_BUDGET,
                full_read_hint=f"用 `cheese_doc(action=get)`{where}读全文",
            ),
        )
    if doc:
        sections["doc"] = piece(
            "opening/doc",
            doc=fit_doc_to_budget(
                doc,
                TOPIC_DOC_CHAR_BUDGET,
                full_read_hint="用 `cheese_doc(action=get)` 读全文",
            ),
        )
    elif doc is not None:
        sections["doc"] = piece("opening/doc_empty")
    if keeps_memory and memory is not None and not memory.is_empty():
        index_text = "\n\n".join(
            f"### {section.label}（`{section.prefix}/`）\n{section.text}"
            for section in memory.sections
        )
        sections["memory"] = memory_block(index_text, list(memory.warnings))
    if environment:
        # 会话开场，不是本轮：机器多大不会变；上次的清单是给「不在场的那一轮」看
        # 的，会话活着的时候它自己的历史就是答案。
        sections["environment"] = piece(
            "opening/environment", lines="\n".join(environment)
        )
    return SessionOpening(sections)


def task_opening_prompt(
    *,
    title: str,
    owner: str | None,
    source: str,
    materials: str = "",
    reviewer: str | None = None,
    has_checkout: bool,
    reports_to_main_line: bool = False,
) -> str:
    """What a new task's agent is told first: where the task came from, what
    was put on the table there, and that it goes to the work from there; the
    task's document is written along the way, not before. Who accepts its
    deliveries is said when that is not the owner. How the task ends is not
    presumed here: the task section of every turn says it (`task_section`)."""
    return piece(
        "opening/task_opening",
        title=title,
        who=f"负责人是 @{owner}。" if owner else "",
        source=source,
        put=piece("opening/task_opening_materials", materials=materials)
        if materials
        else "",
        accepted=piece("opening/task_opening_reviewer", reviewer=reviewer)
        if reviewer and reviewer != owner
        else "",
        start=piece(
            "opening/task_opening_checkout"
            if has_checkout
            else "opening/task_opening_plain"
        ),
    ) + (
        # A task the channel's main line opened reports to it: it asks the
        # members, or answers itself.
        "\n" + piece("opening/task_reports") if reports_to_main_line else ""
    )
