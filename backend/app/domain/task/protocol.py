"""机构协议 (spec §4.2) — what a 赛题 offers and what it requires in return.

An institution publishes a 题目板 (`space`, a course / an activity) whose
题目分组 (`space_categories`: 作业, 实验, 小测) hold 题目 (`task`). Linking a
project to a 题目 accepts a protocol: the institution provides a 资源包 (compute
credits, a default expert role), the project accepts 条件 (e.g. 结题答辩 must be
accepted by a mentor).

**Where the protocol lives (#370, decided 2026-08-17).** On the 题目分组, with
an optional per-题目 override — option (c). The reason is the real usage: 作业
has twenty 题目 and one set of terms, so a teacher configures it once. Putting it
only on the 题目 would mean twenty copies to keep in step; putting it only on the
分组 would leave no way to say "this one 题目 gets more compute". The override
is a whole-key replacement, not a deep merge — a half-inherited resource pack is
harder to reason about than either source alone.

This replaces the cheesex `task_templates` table, which held these same four
fields for a parallel 题目 hierarchy nobody could create from the UI. The 知是
side already had the levels; it was missing only the protocol.

Prose elsewhere still calls the 题目分组 a 项目集 — the name this level carried
when it WAS the course. `app.domain.space.models.SpaceCategory` is the row.

**教学配置 (#8d772257).** 一门课不只是给额度，它还教。`teaching` 键承载这件事 ——
本周范围、课程级 system prompt、本周要用的课件。它走**和其他键完全相同的三级**
（题目分组 → 题目 `protocol_override` 整键覆盖 → 项目 `settings` 覆盖），因为给
教育字段另起一套继承规则，就等于多一份要同步的规则，第一次有人只改其中一处就会
两边不一致。
"""

from dataclasses import dataclass, field
from typing import Any

#: Keys a 题目 may override. Anything else on the 分组 is inherited as-is.
#: `shell` rides the same chain on purpose: whether a 项目集 is a 创研课 or an
#: 办公 workspace is the same kind of statement as what it provides, and it
#: needs the same "configure it once, override this one 题目" behaviour. See
#: `app.domain.shell` for what the value means; here it is only a name.
#: `teaching` rides it for the same reason, with one more: a second inheritance
#: rule for the course's fields would be a second thing to keep in step, and the
#: two would disagree the first time someone edited one of them.
_OVERRIDABLE = ("resource_pack", "conditions", "default_role", "shell", "teaching")


def _text(value: Any) -> str | None:
    """A non-blank string, or None. Whitespace-only is None: a field a teacher
    tabbed through is not a configuration."""
    return value if isinstance(value, str) and value.strip() else None


def _week(value: Any) -> int | None:
    """A week number. Bools are rejected for `compute_credits`' reason — `True`
    is an int in Python and would silently become 第 1 周."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _texts(value: Any) -> list[str]:
    """The non-blank strings of a list, in order, deduplicated. A config is a
    thing a person types, so a repeated line is a typo, not two entries."""
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for item in value:
        text = _text(item)
        if text is not None:
            seen.setdefault(text.strip(), None)
    return list(seen)


def _ids(value: Any) -> list[int]:
    """Positive int ids, in order, deduplicated. Same reasoning as `_texts`."""
    if not isinstance(value, list):
        return []
    seen: dict[int, None] = {}
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            continue
        seen.setdefault(item, None)
    return list(seen)


@dataclass(frozen=True)
class Teaching:
    """课程级教学配置 — the frame a 创研课's agents work inside THIS week.

    It is a 项目集-level thing (二十个赛题, 一份教学安排) that rides the same
    three levels as the rest of the protocol. Everything in it changes weekly,
    which is why none of it is baked into a project at creation: the week is a
    fact about NOW, and a copy taken at registration is wrong by week two.

    **It is read, never taught.** Nothing here is content the agent must learn —
    it is scope. `allowed_topics` says what this week is about, `avoid_in_code`
    what not to reach for yet; an agent that ignores both still works, it just
    works on next month's material.
    """

    #: Course-level system prompt TEMPLATE. `{current_week}` / `{allowed_topics}`
    #: / `{avoid_in_code}` inside it are filled from the three fields below —
    #: filled by literal replacement, never `str.format`, so a 讲义 that quotes
    #: `{"key": ...}` at the agent cannot take the turn down with it.
    system_prompt: str | None = None
    #: Which week of the course this is. None = the course did not say.
    current_week: int | None = None
    #: What this week covers, in the teacher's words.
    allowed_topics: list[str] = field(default_factory=list)
    #: What the course has not taught yet — constructs a solution must not lean
    #: on, e.g. `["递归"]` in a week that has only reached loops.
    avoid_in_code: list[str] = field(default_factory=list)
    #: 课件/知识材料 by REFERENCE, not by copy. Resolved to names and links when
    #: the prompt is built (`app.domain.task.teaching`), so a renamed 课件 or a
    #: rotated link is picked up without re-saving the 项目集 — and so this key
    #: stays a small JSON blob rather than a second copy of the material library.
    material_ids: list[int] = field(default_factory=list)
    knowledge_ids: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Nothing configured. Callers branch on this to skip the lookups that
        resolve the references — a non-course project must not pay a query, and
        must not put a word into its prompt, for a configuration it has none
        of."""
        return not (
            self.system_prompt
            or self.current_week is not None
            or self.allowed_topics
            or self.avoid_in_code
            or self.material_ids
            or self.knowledge_ids
        )

    def fill(self, template: str) -> str:
        """The `system_prompt` template with this week's variables substituted.

        Plain `str.replace` over the three known tokens. `str.format` would
        treat every `{` in a teacher's prose as a field and raise on the ones it
        does not recognize — a 讲义 quoting a dict literal would break every
        turn of the course, and the failure would name a brace rather than the
        config that caused it.
        """
        allowed = "、".join(self.allowed_topics) or "（未指定）"
        avoid = "、".join(self.avoid_in_code) or "（未指定）"
        week = str(self.current_week) if self.current_week is not None else "（未指定）"
        for token, value in (
            ("{current_week}", week),
            ("{allowed_topics}", allowed),
            ("{avoid_in_code}", avoid),
        ):
            template = template.replace(token, value)
        return template

    @classmethod
    def from_json(cls, raw: Any) -> "Teaching":
        """Read what a JSON column holds. NEVER raises, and never a partial
        failure.

        `resolve` runs on every turn of every project, so a malformed value
        reaching it must not take the turn down: a project whose 项目集 carries a
        typo in one field has to keep working on the other five. Junk is
        dropped, not raised — the strict check belongs at the edit endpoint,
        where a person is looking at the form and can be told what is wrong.
        """
        if not isinstance(raw, dict):
            return cls()
        return cls(
            system_prompt=_text(raw.get("system_prompt")),
            current_week=_week(raw.get("current_week")),
            allowed_topics=_texts(raw.get("allowed_topics")),
            avoid_in_code=_texts(raw.get("avoid_in_code")),
            material_ids=_ids(raw.get("material_ids")),
            knowledge_ids=_ids(raw.get("knowledge_ids")),
        )


@dataclass(frozen=True)
class Protocol:
    """The effective terms for one 赛题."""

    #: What the institution provides. `{"compute_credits": N}` issues a grant.
    resource_pack: dict[str, Any] = field(default_factory=dict)
    #: What the project accepts, e.g.
    #: ``{"required_topic": "结题答辩", "reviewer_role": "mentor"}``.
    conditions: list[dict[str, Any]] = field(default_factory=list)
    #: Expert role a project inherits when it has none of its own (spec §8.2).
    default_role: str | None = None
    #: Which 壳 the projects under this protocol run: a NAME, resolved against
    #: `app.domain.shell.catalog.CATALOG`. The declaration is platform-owned and
    #: the institution only picks one, so a 项目集 cannot grow a private 壳 that
    #: no other 项目集 can use — and cannot ship code, which is the same rule
    #: (`app.domain.shell`). None = nobody said, so `default` is in force.
    shell: str | None = None

    #: 课程级教学配置 — what the course wants its agents to know THIS week. Empty
    #: for anything that is not a course, which is every project that existed
    #: before this key did.
    teaching: Teaching = field(default_factory=Teaching)

    @property
    def compute_credits(self) -> float:
        """Credits to grant, or 0. Bools are rejected on purpose — `True` is an
        int in Python and would silently become a one-credit grant."""
        value = self.resource_pack.get("compute_credits")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return 0.0
        return float(value) if value > 0 else 0.0

    def mentor_required_for(self, topic_title: str) -> bool:
        """Does a condition demand a mentor accept a topic with this title?

        A condition with an EMPTY ``required_topic`` matches nothing. It used to
        match everything, which turned one blank field into "every topic in this
        project needs a mentor" — a footgun a teacher cannot see from the form.
        """
        return any(
            c.get("reviewer_role") == "mentor"
            and (c.get("required_topic") or "").strip()
            and c["required_topic"].strip() in topic_title
            for c in self.conditions
        )


def _project_override(project: Any | None) -> dict[str, Any]:
    """A project's own protocol overrides, read off `Project.settings`.

    The keys are the protocol's own names, written FLAT — the same place, and
    the same key, `app.domain.shell.service.SHELL_KEY` reads for a project's 壳.
    A nested `settings["protocol"]` was the alternative; it lost because the
    project level is one level, not one per key, and 壳 already says so in a
    flat key. Two spellings of the same level is the drift this chain exists to
    have exactly once.

    `settings` is free-form and holds unrelated keys (`forge_kind`, the compute
    profile), so anything not named in `_OVERRIDABLE` is never read: a stranger
    key cannot be mistaken for a protocol field.
    """
    settings = getattr(project, "settings", None)
    return settings if isinstance(settings, dict) else {}


def resolve(
    *, category: Any | None, task: Any | None, project: Any | None = None
) -> Protocol:
    """The terms in force for one project: three levels, most specific last.

    题目分组 → 题目 (`protocol_override`) → 项目 (`settings`). Every key takes the
    same trip, `shell` and `teaching` included: the levels are a property of the
    protocol, not of any one field in it, so a field added here is overridable
    at all three the day it is added rather than when someone remembers to wire
    it up again.

    Each level replaces a key WHOLE — no deep merge, for the reason the 分组
    placement was chosen: a half-inherited resource pack is harder to reason
    about than either source alone.

    All three arguments are optional so callers do not have to branch: a 题目
    with no 分组, or a project with no 题目 at all, resolves to an empty protocol
    — which is the 项目自治 default (spec §4), not an error.
    """
    values: dict[str, Any] = {
        "resource_pack": getattr(category, "resource_pack", None) or {},
        "conditions": getattr(category, "conditions", None) or [],
        "default_role": getattr(category, "default_role", None),
        "shell": getattr(category, "shell", None),
        "teaching": getattr(category, "teaching", None) or {},
    }
    for source in (
        getattr(task, "protocol_override", None) or {},
        _project_override(project),
    ):
        for key in _OVERRIDABLE:
            if key in source and source[key] is not None:
                values[key] = source[key]
    return Protocol(
        resource_pack=dict(values["resource_pack"] or {}),
        conditions=list(values["conditions"] or []),
        default_role=values["default_role"] or None,
        shell=values["shell"] or None,
        teaching=Teaching.from_json(values["teaching"]),
    )
