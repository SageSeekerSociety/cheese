"""Drafts: a 题目 being written, or the unsaved changes to a published one.

A draft is the publish form as its writer left it (``TaskDraft.fields``, in the
shape ``POST /tasks`` takes), kept on the server. Two hands write into it:

* **The person**, through the page: every change to the form is saved as they
  make it. What they write is theirs, and the draft remembers when they last
  wrote each field (``TaskDraft.marks``).
* **Their 芝士**, through its tools, while it answers. It writes the same
  fields in its own words (``AGENT_FIELDS``: names rather than ids, a
  deadline on the person's own clock, Markdown for the description), and it
  writes from what it last read: a field the person changed after that reading
  is left as the person left it, and 芝士 is told so (``Written.kept``).

A draft ends by being published — a new 题目, or the changes saved onto the
published one — and is gone then.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.task.models import TaskDraft
from app.domain.task.task_service import TaskService
from app.domain.user.models import User, UserProfile

#: The form's fields, as the page and ``POST /tasks`` name them. Three are the
#: page's own and never reach ``POST /tasks`` (``PAGE_ONLY``):
#: ``descriptionMarkdown`` (the description as Markdown, which is what 芝士
#: reads and edits), ``teachingOwn`` (whether the 题目 has its own AI 队友辅导)
#: and ``attachmentFiles`` (the names the page lists beside ``attachmentIds``).
FORM_FIELDS = frozenset(
    {
        "name",
        "description",
        "descriptionMarkdown",
        "submitterType",
        "rank",
        "deadline",
        "registrationStartAt",
        "defaultDeadline",
        "participantLimit",
        "minTeamSize",
        "maxTeamSize",
        "teamLockingPolicy",
        "categoryId",
        "topics",
        "requireRealName",
        "accessControlEnabled",
        "accessDomainGroupIds",
        "mentors",
        "submissionSchema",
        "rubric",
        "rubricPublic",
        "attachmentIds",
        "attachmentFiles",
        "teaching",
        "teachingOwn",
    }
)
#: The page's own fields, left out of what is published: the files' names the
#: page lists beside ``attachmentIds`` are one of them.
PAGE_ONLY = frozenset({"descriptionMarkdown", "teachingOwn", "attachmentFiles"})

#: The longest a draft's whole form may be, as JSON.
MAX_FORM_BYTES = 200_000
#: How many drafts one person may keep in one space.
MAX_DRAFTS_PER_SPACE = 50
#: How long a 题目's intro is: the start of its description, as text.
INTRO_CHARS = 255

#: What 芝士 writes, field by field, and the form field each one lands in. The
#: order is the form's, top to bottom.
AGENT_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "description": ("description", "descriptionMarkdown"),
    "submission": ("submissionSchema",),
    "rubric": ("rubric",),
    "rubric_public": ("rubricPublic",),
    "form": ("submitterType",),
    "team_min": ("minTeamSize",),
    "team_max": ("maxTeamSize",),
    "difficulty": ("rank",),
    "participant_limit": ("participantLimit",),
    "deadline": ("deadline",),
    "days_to_submit": ("defaultDeadline",),
    "category": ("categoryId",),
    "tags": ("topics",),
    "real_name": ("requireRealName",),
    "registration_start": ("registrationStartAt",),
    "team_locking": ("teamLockingPolicy",),
    "mentors": ("mentors",),
    "access": ("accessControlEnabled", "accessDomainGroupIds"),
    "attachments": ("attachmentIds", "attachmentFiles"),
    "ai_guidance": ("teaching", "teachingOwn"),
}

# i18n-data: what 芝士 writes and reads, matched against its words.
FORMS = {"个人": "USER", "团队": "TEAM"}
# i18n-data: the difficulty levels as the form names them.
DIFFICULTIES = {"初级": 1, "中级": 2, "高级": 3}
# i18n-data: what a submission item is handed in as.
KINDS = {"文件": "FILE", "文字": "TEXT"}
# i18n-data: whether a team's members may change once it is approved.
LOCKING = {"允许变更": "NO_LOCK", "报名通过后锁定": "LOCK_ON_APPROVAL"}

_WHEN = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\s*$")


@dataclass(frozen=True)
class Space:
    """What a draft's fields are checked against, in 芝士's words: the space's
    categories and tags by name, and the person's clock."""

    id: int
    name: str
    categories: dict[str, int]
    tags: dict[str, int]
    zone: tzinfo
    #: Who may be a mentor: the person writing and the space's admins, by
    #: handle, each with the name they go by.
    mentors: dict[str, str] = field(default_factory=dict)
    #: The space's e-mail domain groups by name: who a 题目 may be limited to.
    domain_groups: dict[str, int] = field(default_factory=dict)
    #: The space's 题目 templates (``Space.task_templates``): a name, what it is
    #: for, and the form it fills in.
    templates: tuple[dict, ...] = ()


@dataclass
class Written:
    """What one write by 芝士 did: the fields it changed, each with what it
    was before (for undoing), and the ones it left because the person had
    changed them since 芝士 read the draft."""

    draft: TaskDraft
    created: bool = False
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)


#: The clock a person who never said theirs is on.
DEFAULT_ZONE = "Asia/Shanghai"


class SpaceReads(Protocol):
    """What a draft needs to know of its space (``SpaceService`` has it)."""

    async def get_space(self, space_id: int) -> Any: ...

    async def list_categories(self, space_id: int) -> Any: ...

    async def list_classification_topics(self, space_id: int) -> Any: ...

    async def list_admins(self, space_id: int) -> Any: ...

    async def list_domain_groups(
        self, *, space_id: int, actor_user_id: int | None
    ) -> Any: ...


async def terms(
    session: AsyncSession,
    spaces: SpaceReads,
    space_id: int,
    zone_name: str | None,
    *,
    user_id: int,
) -> Space:
    """The space's categories, tags, mentors and domain groups by name, and
    the person's clock."""
    space = await spaces.get_space(space_id)
    if space is None:
        raise NotFoundError.for_resource("space", space_id)
    categories = await spaces.list_categories(space_id)
    tags = await spaces.list_classification_topics(space_id)
    groups = await spaces.list_domain_groups(space_id=space_id, actor_user_id=user_id)
    admins = [a.user_id for a in await spaces.list_admins(space_id)]
    try:
        zone: tzinfo = ZoneInfo(zone_name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_ZONE)
    return Space(
        id=space_id,
        name=space.name,
        categories={c.name: c.id for c in categories},
        tags={t.name: t.id for t in tags},
        zone=zone,
        mentors=await _people(session, [user_id, *admins]),
        domain_groups={g.name: g.id for g, _ in groups},
        templates=tuple(
            t
            for t in (getattr(space, "task_templates", None) or [])
            if isinstance(t, dict) and str(t.get("name") or "").strip()
        ),
    )


async def _people(session: AsyncSession, user_ids: list[int]) -> dict[str, str]:
    """Handle -> the name each goes by, in the order given."""
    ids = list(dict.fromkeys(user_ids))
    rows = await session.execute(
        select(User.id, User.username, UserProfile.nickname)
        .outerjoin(
            UserProfile,
            (UserProfile.user_id == User.id) & UserProfile.deleted_at.is_(None),
        )
        .where(User.id.in_(ids), User.deleted_at.is_(None))
    )
    found = {uid: (handle, nickname or handle) for uid, handle, nickname in rows}
    return dict(found[uid] for uid in ids if uid in found)


async def task_names(session: AsyncSession, rows: list[TaskDraft]) -> dict[int, str]:
    """The names of the published 题目 these drafts change."""
    ids = [r.task_id for r in rows if r.task_id]
    if not ids:
        return {}
    found = await TaskService.of(session).get_tasks_by_ids(ids)
    return {task_id: task.name for task_id, task in found.items()}


async def context(
    session: AsyncSession, spaces: SpaceReads, row: TaskDraft, zone_name: str | None
) -> str:
    """The draft as 芝士 is shown it: with a question asked on its page, and
    when it reads it."""
    space = await terms(session, spaces, row.space_id, zone_name, user_id=row.user_id)
    names = await task_names(session, [row])
    name = names.get(row.task_id or 0)
    return scene(row, name) + "\n" + agent_view(row, space, task_name=name)


def scene(draft: TaskDraft, task_name: str | None = None) -> str:
    """What the page a draft is open on is for, and what a question asked
    there means: told with the draft, so the rules stay the same on every
    page."""
    if draft.task_id:
        lines = [
            f"修改题目页：左边的表单是已发布题目「{task_name}」的修改（草稿 {draft.id}），"
            "用户保存之前，学生看到的还是原来的题目。",
            "- 用户说「这道题」「表单」，指这道题。",
            "- 用户只附上文件、没说要做什么时，按文件内容修改这道题（给 draft），"
            "不新建草稿。",
            "- 用户说保存时，用 cheese_task_publish 保存修改。",
        ]
    else:
        lines = [
            f"发布题目页：左边是一份新题目的表单（草稿 {draft.id}），用户和你一起写这道题，"
            "写好后发布。",
            "- 用户说「这道题」「表单」，指这份草稿。",
            "- 用户只附上文件、没说要做什么时，是要从文件导入题目：读完文件，把里面的"
            "题目写进表单。",
            "- 这份草稿尚未填写时，文件里的第一道题写进它（给 draft），不要另建；其余每道"
            "题各新建一份草稿（给 space，source 写文件名）。草稿已经填写了，就不要覆盖它，"
            "文件里的题都新建。",
            "- 用户说发布时，用 cheese_task_publish 发布。",
        ]
    return "<这一页>\n" + "\n".join(lines) + "\n</这一页>"


# --- reading and keeping -----------------------------------------------------


async def mine(session: AsyncSession, user_id: int, draft_id: int) -> TaskDraft:
    """The person's draft; somebody else's reads as missing, so an id confirms
    nothing."""
    row = await session.get(TaskDraft, draft_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError.for_resource("draft", draft_id)
    return row


async def list_mine(
    session: AsyncSession, user_id: int, space_id: int
) -> list[TaskDraft]:
    rows = await session.scalars(
        select(TaskDraft)
        .where(TaskDraft.user_id == user_id, TaskDraft.space_id == space_id)
        .order_by(TaskDraft.updated_at.desc())
        .limit(MAX_DRAFTS_PER_SPACE)
    )
    return list(rows)


async def for_task(
    session: AsyncSession, user_id: int, task_id: int
) -> TaskDraft | None:
    """The person's unsaved changes to a published 题目, if they have some."""
    return await session.scalar(
        select(TaskDraft)
        .where(TaskDraft.user_id == user_id, TaskDraft.task_id == task_id)
        .order_by(TaskDraft.updated_at.desc())
        .limit(1)
    )


async def start(
    session: AsyncSession,
    *,
    user_id: int,
    space_id: int,
    fields: dict | None = None,
    task_id: int | None = None,
    source: str = "",
    conversation_id=None,
) -> TaskDraft:
    count = len(await list_mine(session, user_id, space_id))
    if count >= MAX_DRAFTS_PER_SPACE:
        raise BadRequestError(say("draftTooMany", max=MAX_DRAFTS_PER_SPACE))
    row = TaskDraft(
        user_id=user_id,
        space_id=space_id,
        task_id=task_id,
        source=source[:255],
        conversation_id=conversation_id,
        fields={},
        marks={},
        version=0,
    )
    if fields:
        _check_form(fields)
        row.fields = dict(fields)
    session.add(row)
    await session.flush()
    return row


def _check_form(fields: dict) -> None:
    unknown = sorted(set(fields) - FORM_FIELDS)
    if unknown:
        raise BadRequestError(say("draftUnknownFields", fields=", ".join(unknown)))
    if len(json.dumps(fields, ensure_ascii=False)) > MAX_FORM_BYTES:
        raise BadRequestError(say("draftTooLarge"))


def person_writes(draft: TaskDraft, fields: dict) -> list[str]:
    """The person changed these fields on the page. Their write always lands,
    and is remembered field by field."""
    _check_form(fields)
    merged = {**(draft.fields or {}), **fields}
    _check_form(merged)
    draft.version += 1
    draft.fields = merged
    draft.marks = {**(draft.marks or {}), **dict.fromkeys(fields, draft.version)}
    draft.updated_at = datetime.now(UTC)
    return sorted(fields)


def cheese_writes(draft: TaskDraft, fields: dict, *, read_at: int | None) -> Written:
    """芝士 writes these form fields, having read the draft at version
    ``read_at`` (now, when it says nothing). A field the person changed after
    that is kept as the person left it."""
    since = draft.version if read_at is None else read_at
    marks = draft.marks or {}
    before = dict(draft.fields or {})
    after = dict(before)
    written = Written(draft)
    for agent_key, targets in AGENT_FIELDS.items():
        if not any(target in fields for target in targets):
            continue
        if any(int(marks.get(target, 0)) > since for target in targets):
            written.kept.append(agent_key)
            continue
        for target in targets:
            if target in fields and before.get(target) != fields[target]:
                after[target] = fields[target]
                written.changed[target] = (before.get(target), fields[target])
    if written.changed:
        _check_form(after)
        draft.version += 1
        draft.fields = after
        draft.updated_at = datetime.now(UTC)
    return written


# --- 芝士's words ------------------------------------------------------------


def from_agent(
    args: dict, space: Space, current: dict, files: list[dict] | None = None
) -> dict:
    """What 芝士 asked to write, as form fields. Refuses, with what to write
    instead, a value the form would not take: a category the space does not
    have, a deadline it cannot read. ``files`` are the attachments it named
    (``attachments``), already found to be the person's: id, name and size."""
    out: dict = _template(args["template"], space) if "template" in args else {}
    if "name" in args:
        name = str(args["name"] or "").strip()
        if not name or len(name) > 100:
            raise BadRequestError(say("draftNameLength"))
        out["name"] = name
    description = _description(args, current)
    if description is not None:
        out["description"] = description
        out["descriptionMarkdown"] = description
    if "form" in args:
        if args["form"] not in FORMS:
            raise BadRequestError(say("draftFormChoice"))
        out["submitterType"] = FORMS[args["form"]]
    for key, target in (("team_min", "minTeamSize"), ("team_max", "maxTeamSize")):
        if key in args:
            out[target] = _count(args[key], key, low=1, high=100)
    if "difficulty" in args:
        if args["difficulty"] not in DIFFICULTIES:
            raise BadRequestError(say("draftDifficultyChoice"))
        out["rank"] = DIFFICULTIES[args["difficulty"]]
    if "participant_limit" in args:
        limit = args["participant_limit"]
        out["participantLimit"] = (
            None if limit in (None, 0, "") else _count(limit, "participant_limit")
        )
    if "deadline" in args:
        out["deadline"] = _deadline(args["deadline"], space.zone)
    if "days_to_submit" in args:
        out["defaultDeadline"] = _count(args["days_to_submit"], "days_to_submit")
    if "category" in args:
        name = str(args["category"] or "").strip()
        if name not in space.categories:
            raise BadRequestError(
                say("draftCategoryChoice", names="、".join(space.categories) or "—")
            )
        out["categoryId"] = space.categories[name]
    if "tags" in args:
        names = [str(n).strip() for n in args["tags"] or [] if str(n).strip()]
        unknown = [n for n in names if n not in space.tags]
        if unknown:
            raise BadRequestError(
                say(
                    "draftTagChoice",
                    unknown="、".join(unknown),
                    names="、".join(space.tags) or "—",
                )
            )
        out["topics"] = [space.tags[n] for n in names]
    if "submission" in args:
        out["submissionSchema"] = _submission(args["submission"])
    if "rubric" in args:
        out["rubric"] = _rubric(args["rubric"])
    if "rubric_public" in args:
        out["rubricPublic"] = bool(args["rubric_public"])
    if "real_name" in args:
        out["requireRealName"] = bool(args["real_name"])
    if "registration_start" in args:
        out["registrationStartAt"] = _deadline(args["registration_start"], space.zone)
    if "team_locking" in args:
        if args["team_locking"] not in LOCKING:
            raise BadRequestError(say("draftLockingChoice"))
        out["teamLockingPolicy"] = LOCKING[args["team_locking"]]
    if "mentors" in args:
        out["mentors"] = _mentors(args["mentors"], space)
    if "access" in args:
        ids = _groups(args["access"], space)
        out["accessControlEnabled"] = bool(ids)
        out["accessDomainGroupIds"] = ids
    if "attachments" in args:
        given = list(files or [])
        out["attachmentIds"] = [f["id"] for f in given]
        out["attachmentFiles"] = given
    if "ai_guidance" in args:
        text = str(args["ai_guidance"] or "").strip()
        out["teaching"] = {"systemPrompt": text} if text else {}
        out["teachingOwn"] = bool(text)
    return out


def _mentors(given, space: Space) -> list[str]:
    """Mentors by handle or by the name they go by; only the person and the
    space's admins may be one."""
    by_name = {name: handle for handle, name in space.mentors.items()}
    out: list[str] = []
    for raw in given or []:
        word = str(raw or "").strip().lstrip("@")
        handle = word if word in space.mentors else by_name.get(word)
        if handle is None:
            raise BadRequestError(
                say("draftMentorChoice", name=word, names=_people_said(space))
            )
        if handle not in out:
            out.append(handle)
    return out


def _groups(given, space: Space) -> list[int]:
    names = [str(n).strip() for n in given or [] if str(n).strip()]
    unknown = [n for n in names if n not in space.domain_groups]
    if unknown:
        raise BadRequestError(
            say(
                "draftAccessChoice",
                unknown="、".join(unknown),
                names="、".join(space.domain_groups) or "—",
            )
        )
    return list(dict.fromkeys(space.domain_groups[n] for n in names))


def _people_said(space: Space) -> str:
    return (
        "、".join(f"{name}（{handle}）" for handle, name in space.mentors.items())
        or "—"
    )


def _description(args: dict, current: dict) -> str | None:
    """The description 芝士 writes: whole, or the current one with passages
    replaced (``description_edits``, each ``{"find", "replace"}``, ``find``
    occurring exactly once)."""
    text = args.get("description")
    edits = args.get("description_edits") or []
    if text is None and not edits:
        return None
    if text is not None:
        text = str(text)
    else:
        text = markdown_of(current)
    for edit in edits:
        find = str((edit or {}).get("find") or "")
        replace = str((edit or {}).get("replace") or "")
        if not find:
            raise BadRequestError(say("draftEditNeedsFind"))
        count = text.count(find)
        if count != 1:
            raise BadRequestError(
                say("draftEditNotFound" if count == 0 else "draftEditNotUnique")
            )
        text = text.replace(find, replace)
    return text


def _count(value, key: str, *, low: int = 1, high: int = 10_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low - 1
    if not low <= number <= high:
        raise BadRequestError(say("draftNumberRange", field=key, low=low, high=high))
    return number


def _deadline(value, zone: tzinfo) -> int | None:
    """A deadline on the person's clock, ``YYYY-MM-DD HH:MM`` (a day alone is
    its last minute), as the form keeps it: milliseconds. Empty is none."""
    if value in (None, ""):
        return None
    found = _WHEN.match(str(value))
    if found is None:
        raise BadRequestError(say("draftDeadlineFormat"))
    year, month, day, hour, minute = found.groups()
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour) if hour is not None else 23,
            int(minute) if minute is not None else 59,
            tzinfo=zone,
        )
    except ValueError as exc:
        raise BadRequestError(say("draftDeadlineFormat")) from exc
    return int(moment.timestamp() * 1000)


def _submission(items) -> list[dict]:
    out = []
    for item in items or []:
        name = str((item or {}).get("name") or "").strip()
        kind = (item or {}).get("kind") or "文件"
        if not name:
            continue
        if kind not in KINDS:
            raise BadRequestError(say("draftSubmissionKind"))
        out.append({"prompt": name[:255], "type": KINDS[kind]})
    if not out:
        raise BadRequestError(say("draftSubmissionEmpty"))
    return out


def _rubric(lines) -> list[dict]:
    out = []
    for line in lines or []:
        line = line or {}
        try:
            points = int(line.get("points") or 0)
        except (TypeError, ValueError):
            points = -1
        out.append(
            {
                "item": str(line.get("item") or "").strip(),
                "points": points,
                "criteria": str(line.get("criteria") or "").strip(),
            }
        )
    return out


def markdown_of(fields: dict) -> str:
    """The description as Markdown: what the page last saved of it, or the
    description itself when it was written as Markdown."""
    md = fields.get("descriptionMarkdown")
    if isinstance(md, str):
        return md
    description = fields.get("description")
    if isinstance(description, str) and not _is_doc_json(description):
        return description
    return ""


def _is_doc_json(text: str) -> bool:
    if not text.lstrip().startswith("{"):
        return False
    try:
        return json.loads(text).get("type") == "doc"
    except (ValueError, AttributeError):
        return False


def agent_view(draft: TaskDraft, space: Space, *, task_name: str | None = None) -> str:
    """The draft as 芝士 reads it: every field in its own words, the way it
    writes them back."""
    head = (
        f"草稿 {draft.id}（{task_name} 的未保存修改）"
        if draft.task_id
        else f"草稿 {draft.id}（新题目，尚未填写）"
        if blank(draft.fields or {})
        else f"草稿 {draft.id}（新题目）"
    )
    return _view(
        f"<草稿 id={draft.id} version={draft.version} 空间id={space.id} 空间={space.name}>",
        head,
        draft.fields or {},
        space,
        "</草稿>",
    )


def published_view(task_id: int, name: str, fields: dict, space: Space) -> str:
    """A published 题目 as 芝士 reads it, in the same words as a draft."""
    return _view(
        f"<题目 id={task_id} 空间id={space.id} 空间={space.name}>",
        f"题目 {task_id}（已发布）：{name}",
        fields,
        space,
        "</题目>",
    )


def _template(name, space: Space) -> dict:
    """The form a space template fills in, as 芝士's 「使用模板」 does on the
    page: only what the template says; the fields after it in the same write
    are written over it."""
    wanted = str(name or "").strip()
    found = next((t for t in space.templates if t.get("name") == wanted), None)
    if found is None:
        raise BadRequestError(
            say(
                "draftTemplateChoice",
                names="、".join(str(t.get("name")) for t in space.templates) or "—",
            )
        )
    out: dict = {}
    if str(found.get("title") or "").strip():
        out["name"] = str(found["title"]).strip()[:100]
    content = found.get("content")
    text = (
        doc_markdown(content)
        if isinstance(content, str) and _is_doc_json(content)
        else str(content or "")
    )
    if text.strip():
        out["description"] = text
        out["descriptionMarkdown"] = text
    for key in (
        "submitterType",
        "rank",
        "minTeamSize",
        "maxTeamSize",
        "defaultDeadline",
        "requireRealName",
    ):
        if found.get(key) is not None:
            out[key] = found[key]
    return out


def doc_markdown(text: str) -> str:
    """An editor document (TipTap JSON, as a template keeps its description)
    as Markdown: headings, paragraphs, lists, quotes, code and inline marks."""

    def inline(nodes) -> str:
        parts = []
        for node in nodes or []:
            if node.get("type") == "hardBreak":
                parts.append("  \n")
                continue
            if node.get("type") != "text":
                parts.append(inline(node.get("content")))
                continue
            value = str(node.get("text") or "")
            for mark in node.get("marks") or []:
                kind = mark.get("type")
                if kind == "bold":
                    value = f"**{value}**"
                elif kind == "italic":
                    value = f"*{value}*"
                elif kind == "code":
                    value = f"`{value}`"
            parts.append(value)
        return "".join(parts)

    def block(node, indent: str = "") -> list[str]:
        kind = node.get("type")
        content = node.get("content") or []
        if kind == "heading":
            level = int((node.get("attrs") or {}).get("level") or 2)
            return [f"{'#' * min(max(level, 1), 6)} {inline(content)}"]
        if kind == "paragraph":
            return [indent + inline(content)]
        if kind in ("bulletList", "orderedList", "taskList"):
            lines = []
            for n, item in enumerate(content, 1):
                lead = f"{n}. " if kind == "orderedList" else "- "
                inner = [
                    line
                    for child in item.get("content") or []
                    for line in block(child, indent + "  ")
                ]
                if inner:
                    lines.append(indent + lead + inner[0].strip())
                    lines += inner[1:]
            return lines
        if kind == "blockquote":
            return ["> " + line for child in content for line in block(child)]
        if kind == "codeBlock":
            return ["```", inline(content), "```"]
        if kind == "horizontalRule":
            return ["---"]
        return [line for child in content for line in block(child, indent)]

    try:
        doc = json.loads(text)
    except ValueError:
        return text
    blocks = ["\n".join(block(node)) for node in doc.get("content") or []]
    return "\n\n".join(b for b in blocks if b.strip())


def _view(open_tag: str, head: str, fields: dict, space: Space, close_tag: str) -> str:
    by_id = {v: k for k, v in space.categories.items()}
    tag_by_id = {v: k for k, v in space.tags.items()}
    forms = {v: k for k, v in FORMS.items()}
    levels = {v: k for k, v in DIFFICULTIES.items()}
    kinds = {v: k for k, v in KINDS.items()}
    lockings = {v: k for k, v in LOCKING.items()}
    group_by_id = {v: k for k, v in space.domain_groups.items()}

    def when(ms) -> str:
        if not ms:
            return ""
        moment = datetime.fromtimestamp(int(ms) / 1000, tz=UTC).astimezone(space.zone)
        return moment.strftime("%Y-%m-%d %H:%M")

    lines = [
        open_tag,
        head,
        f"name: {fields.get('name') or ''}",
        f"form: {forms.get(str(fields.get('submitterType')), '')}",
    ]
    if fields.get("submitterType") == "TEAM":
        lines.append(
            f"team_min: {fields.get('minTeamSize') or ''}"
            f"  team_max: {fields.get('maxTeamSize') or ''}"
            f"  team_locking: {lockings.get(str(fields.get('teamLockingPolicy')), '允许变更')}"
        )
    lines += [
        f"difficulty: {levels.get(int(fields.get('rank') or 0), '')}",
        f"participant_limit: {fields.get('participantLimit') or '不限'}",
        "mentors: "
        + "、".join(
            f"{space.mentors.get(h, h)}（{h}）" for h in fields.get("mentors") or []
        ),
        f"registration_start: {when(fields.get('registrationStartAt'))}",
        f"deadline: {when(fields.get('deadline'))}",
        f"days_to_submit: {fields.get('defaultDeadline') or ''}",
        f"category: {by_id.get(int(fields.get('categoryId') or 0), '')}",
        "tags: "
        + "、".join(tag_by_id.get(t, str(t)) for t in fields.get("topics") or []),
        "access: "
        + (
            "、".join(
                group_by_id.get(g, str(g))
                for g in fields.get("accessDomainGroupIds") or []
            )
            if fields.get("accessControlEnabled")
            else "空间全部成员"
        ),
        f"real_name: {'是' if fields.get('requireRealName') else '否'}",
        "attachments: "
        + "、".join(
            f"{f.get('name')}（文件 id {f.get('id')}）"
            for f in fields.get("attachmentFiles") or []
            if isinstance(f, dict)
        ),
        "ai_guidance: "
        + (
            str((fields.get("teaching") or {}).get("systemPrompt") or "")
            if fields.get("teachingOwn")
            else "（用空间的默认指导）"
        ),
        "submission:",
        *(
            f"  - {e.get('prompt')}（{kinds.get(e.get('type'), e.get('type'))}）"
            for e in fields.get("submissionSchema") or []
        ),
        f"rubric（对学生{'可见' if fields.get('rubricPublic', True) else '不可见'}）:",
        *(
            f"  - {r.get('item')}｜{r.get('points')} 分｜{r.get('criteria')}"
            for r in fields.get("rubric") or []
        ),
        "description:",
        markdown_of(fields) or "（空）",
        f"这个空间可选的分类：{_names(space.categories)}",
        f"这个空间可选的标签：{_names(space.tags)}",
        f"这个空间的模板：{_templates(space)}",
        f"可以当导师的人：{_people_said(space)}",
        f"这个空间的邮箱域名组：{_names(space.domain_groups)}",
        close_tag,
    ]
    return "\n".join(lines)


#: The most names of a space's categories or tags said with a draft.
CHOICES_SAID = 80


def _templates(space: Space) -> str:
    if not space.templates:
        return "（无）"
    return "；".join(
        str(t.get("name"))
        + (
            f"（{str(t.get('description')).strip()}）"
            if str(t.get("description") or "").strip()
            else ""
        )
        for t in space.templates[:CHOICES_SAID]
    )


def _names(choices: dict[str, int]) -> str:
    names = list(choices)
    if not names:
        return "（无）"
    said = "、".join(names[:CHOICES_SAID])
    return said + (f" 等 {len(names)} 个" if len(names) > CHOICES_SAID else "")


def intro_of(fields: dict) -> str:
    """A 题目's intro: the start of its description, as plain text."""
    text = markdown_of(fields)
    if not text:
        text = _doc_text(fields.get("description"))
    text = re.sub(r"[#>*_`\[\]()|-]+", " ", text)
    return " ".join(text.split())[:INTRO_CHARS]


def _doc_text(description) -> str:
    if not isinstance(description, str) or not _is_doc_json(description):
        return ""
    words: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                words.append(str(node.get("text") or ""))
            for child in node.get("content") or []:
                walk(child)

    walk(json.loads(description))
    return " ".join(words)


def published_form(draft: TaskDraft) -> dict:
    """The draft as ``POST /tasks`` / ``PATCH /tasks/{id}`` take it."""
    fields = {k: v for k, v in (draft.fields or {}).items() if k not in PAGE_ONLY}
    fields["intro"] = intro_of(draft.fields or {})
    if not draft.fields.get("teachingOwn", False):
        fields.pop("teaching", None)
    return fields


def blank(fields: dict) -> bool:
    """Nothing of the 题目 is written yet: no name, no description, no
    rubric — what the page starts a draft with is not writing."""
    return not (
        str(fields.get("name") or "").strip()
        or markdown_of(fields).strip()
        or _doc_text(fields.get("description")).strip()
        or fields.get("rubric")
    )


def missing(draft: TaskDraft) -> list[str]:
    """What still has to be written before the draft can be published, in
    芝士's words."""
    fields = draft.fields or {}
    out = []
    if not str(fields.get("name") or "").strip():
        out.append("name")
    if not (markdown_of(fields).strip() or _doc_text(fields.get("description"))):
        out.append("description")
    if fields.get("submitterType") not in ("USER", "TEAM"):
        out.append("form")
    if fields.get("rank") not in (1, 2, 3):
        out.append("difficulty")
    return out


def task_form(task: dict) -> dict:
    """A published 题目, as the form it would be edited in (the page's
    ``editTaskData``)."""
    return {
        "name": task.get("name") or "",
        "submitterType": task.get("submitterType"),
        "rank": task.get("rank"),
        "defaultDeadline": task.get("defaultDeadline"),
        "registrationStartAt": task.get("registrationStartAt"),
        "deadline": task.get("deadline"),
        "description": task.get("description") or "",
        "requireRealName": bool(task.get("requireRealName")),
        "minTeamSize": task.get("minTeamSize"),
        "maxTeamSize": task.get("maxTeamSize"),
        "participantLimit": task.get("participantLimit"),
        "teamLockingPolicy": task.get("teamLockingPolicy"),
        "categoryId": (task.get("category") or {}).get("id"),
        "topics": [t.get("id") for t in task.get("topics") or [] if t.get("id")],
        "accessControlEnabled": bool(task.get("accessControlEnabled")),
        "accessDomainGroupIds": task.get("accessDomainGroupIds") or [],
        "mentors": [m.get("handle") for m in task.get("mentors") or []],
        "submissionSchema": task.get("submissionSchema") or [],
        "rubric": task.get("rubric") or [],
        "rubricPublic": task.get("rubricPublic", True),
        "teaching": task.get("teaching") or {},
        "teachingOwn": bool(task.get("teaching")),
    }
