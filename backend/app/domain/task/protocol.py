"""机构协议 (spec §4.2) — what a 赛题 offers and what it requires in return.

An institution publishes a 题目板 (`space`, a course / an activity) whose
题目分组 (`space_categories`: 作业, 实验, 小测) hold 题目 (`task`). Linking a
project to a 题目 accepts a protocol: the institution provides a 资源包 (compute
credits — nothing else rides in it) and a default expert role, the project accepts 条件 (e.g. 结题答辩 must be
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
AI 队友怎么帮学生（四种方式之一）、老师写的补充要求、可以读的课件、项目里每个 AI
队友每一轮都带上的技能。控制 AI 的设置只在这一处：资源包里曾经也带过一段 prompt
和一组技能名，只在建项目时抄给默认队友一次，后来的队友拿不到，题目改了也跟不上；
这里不读资源包里的那两个键（#4767）。

**两层同时生效（10-20 空间这一轮）。** 空间 (`Space.teaching`) 说这块板上的 AI 队友
一般怎么帮，题目在它上面再加：自己的补充要求、自己的资料，也可以换一种方式。两层
都算（`Teaching.combine`），不是题目盖掉空间；项目自己的设置有的话整份替换。项目集
那一层拿掉了，原来写在项目集上的挪到了它的题目上（迁移 e5f1a9c3d7b2）。
"""

import uuid
from dataclasses import dataclass, field, replace
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
    """Positive int ids, in order, deduplicated: a config is a thing a person
    picks, so a repeated id is a slip, not two entries."""
    if not isinstance(value, list):
        return []
    seen: dict[int, None] = {}
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            continue
        seen.setdefault(item, None)
    return list(seen)


def _uuids(value: Any) -> list[str]:
    """Document ids, in order, deduplicated; anything that is no uuid dropped."""
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for item in value:
        try:
            seen.setdefault(str(uuid.UUID(str(item))), None)
        except ValueError:
            continue
    return list(seen)


#: How an AI teammate helps a student, as the teacher picks it. None = the
#: level did not say.
MODES = ("guide", "example", "spec", "free")


def _mode(value: Any) -> str | None:
    return value if value in MODES else None


@dataclass(frozen=True)
class Teaching:
    """How a course wants its AI teammates to help: one level's settings, or
    the space's and the 题目's together (`resolve`).

    Two levels, both in force: a 空间 says how its AI teammates help in
    general, a 题目 adds to it (its own requirements and materials) and may
    pick a different way of helping. The 项目集 level that used to sit between
    them is gone (#space-round 10-20): one place per thing to say.

    **It is read, never taught.** Nothing here is content the agent must learn;
    it is how to help and what to read.
    """

    #: How to help (`MODES`): lead with questions, explain with examples, ask
    #: for a goal and a check before writing, or as the student asks.
    mode: str | None = None
    #: The teacher's own requirements, in their words.
    system_prompt: str | None = None
    #: The space's requirements when the 题目's are said too (`combine`): both
    #: are in force, and the agent reads them as two.
    space_prompt: str | None = None
    #: 本节学习目标 — what the student should be able to do by the end. Its
    #: presence is also what makes a 教学安排 EDUCATIONAL: a config with goals
    #: and no `system_prompt` gets the default teaching template (Socratic +
    #: 形态纪律, see `harness.opening`), because "学习目标" is the teacher saying
    #: "this one is here to learn" — and a blank prompt then means "use your
    #: teaching manners", not "have no manners".
    learning_goals: list[str] = field(default_factory=list)
    #: 出题人的**私有**教学指导（#944 的第二种可见性）。与 `system_prompt` 同一个
    #: 机制、同一份继承、同一个「用 prompt 说」的实现路径 —— 区别只在**谁读得到它**：
    #: 它进 AI 队友的 system prompt（队友照它做），但**不进任何学生可读的响应**
    #: （`teaching_to_api` 默认不带它；只有出题人 / 板管理员那几条读写才带）。
    #:
    #: 这正是 #944 那句「教学指令优先用 prompt 实现，不新造权限开关」：出题人要给的
    #: 参考答案、评分口径这类**不该进学生资料集**的东西，写在这段 prompt 里就够，
    #: 不需要再为它造一套可见性权限 —— 不提就是私有，提给谁看是接口那一层的事。
    private_prompt: str | None = None
    #: 课件/知识材料 by REFERENCE, not by copy. Resolved to names and links when
    #: the prompt is built (`app.domain.task.teaching`), so a renamed 课件 or a
    #: rotated link is picked up without re-saving — and so this key stays a
    #: small JSON blob rather than a second copy of the material library.
    material_ids: list[int] = field(default_factory=list)
    #: The team documents the course points its AI teammates at (#3774): their
    #: agents may read these, and no other team document.
    document_ids: list[str] = field(default_factory=list)
    #: Skill names every AI teammate in the project carries, on every turn, on
    #: top of its own (`agent.turn.intake.context.carried_skills`). Read per
    #: turn like the rest of this config, so a teammate added after the project
    #: was built carries them too, and a 题目 that changes its list is heard on
    #: the next turn rather than only by projects built after the change.
    skills: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Nothing configured. Callers branch on this to skip the lookups that
        resolve the references — a non-course project must not pay a query, and
        must not put a word into its prompt, for a configuration it has none
        of."""
        return not (
            self.mode
            or self.system_prompt
            or self.space_prompt
            or self.learning_goals
            or self.private_prompt
            or self.material_ids
            or self.document_ids
            or self.skills
        )

    def fill(self, template: str) -> str:
        """`system_prompt` 模板里的 `{learning_goals}` 换成这一节的目标。

        只认这一个占位符。周次、本周范围、要避开的写法这几个 token 随项目集那一层
        一起被 #4525 删掉了；模板里还写着它们就原样留着 —— 没有值可填时，猜一个
        比留着字面更糟。用 `str.replace` 而不是 `str.format`：讲义里引用
        `{"key": ...}` 不该让整轮挂掉，报错还会指出一个花括号而不是那份配置。
        """
        goals = "、".join(self.learning_goals) or "（未指定）"
        return template.replace("{learning_goals}", goals)

    def combine(self, task: "Teaching") -> "Teaching":
        """This (the space's) and a 题目's together: the 题目's way of helping
        when it picks one, both requirements, both sets of materials and of
        skills."""
        if task.is_empty:
            return self
        if self.is_empty:
            return task
        if replace(task, skills=[]).is_empty:
            # A 题目 that gives skills and says nothing else of how to help
            # leaves the space's guidance as it is, its goals and private
            # guidance included: skills are not guidance.
            return replace(
                self, skills=list(dict.fromkeys([*self.skills, *task.skills]))
            )
        return Teaching(
            mode=task.mode or self.mode,
            system_prompt=task.system_prompt,
            space_prompt=self.system_prompt,
            # 学习目标与出题人私有指导是**题目**那一层说的：空间是「这块板上的课
            # 一般怎么帮」，靶子和评分口径是每道题自己的。
            learning_goals=list(task.learning_goals),
            private_prompt=task.private_prompt,
            material_ids=list(dict.fromkeys([*self.material_ids, *task.material_ids])),
            document_ids=list(dict.fromkeys([*self.document_ids, *task.document_ids])),
            skills=list(dict.fromkeys([*self.skills, *task.skills])),
        )

    @classmethod
    def from_json(cls, raw: Any) -> "Teaching":
        """Read what a JSON column holds. NEVER raises, and never a partial
        failure.

        `resolve` runs on every turn of every project, so a malformed value
        reaching it must not take the turn down. Junk is dropped, not raised —
        the strict check belongs at the edit endpoint, where a person is looking
        at the form and can be told what is wrong.
        """
        if not isinstance(raw, dict):
            return cls()
        return cls(
            mode=_mode(raw.get("mode")),
            system_prompt=_text(raw.get("system_prompt")),
            private_prompt=_text(raw.get("private_prompt")),
            learning_goals=_texts(raw.get("learning_goals")),
            material_ids=_ids(raw.get("material_ids")),
            document_ids=_uuids(raw.get("document_ids")),
            skills=_texts(raw.get("skills")),
        )


@dataclass(frozen=True)
class Protocol:
    """The effective terms for one 赛题."""

    #: 资源包: the compute the institution provides, `{"compute_credits": N}`,
    #: issued as a grant restricted to the project. Nothing else rides in it —
    #: what the AI teammates are told and carry is `teaching`.
    resource_pack: dict[str, Any] = field(default_factory=dict)
    #: What the project accepts, e.g.
    #: ``{"required_topic": "结题答辩", "reviewer_role": "mentor"}``.
    conditions: list[dict[str, Any]] = field(default_factory=list)
    #: Expert role a project inherits when it has none of its own (spec §8.2).
    default_role: str | None = None
    #: Which 壳 the projects under this protocol run: a NAME, resolved against
    #: `app.domain.shell_catalog.CATALOG`. The declaration is platform-owned and
    #: the institution only picks one, so a 项目集 cannot grow a private 壳 that
    #: no other 项目集 can use — and cannot ship code, which is the same rule
    #: (`app.domain.shell`). None = nobody said, so `default` is in force.
    shell: str | None = None

    #: How the course wants its AI teammates to help. Empty for anything that is
    #: not a course, which is every project that existed before this key did.
    teaching: Teaching = field(default_factory=Teaching)

    #: WHICH level the `teaching` above came from — ``"space"`` | ``"task"``
    #: | ``"both"`` | ``"project"``, or None when no level said anything. Filled
    #: by the very loop that picks the value (`_resolve_teaching`): a second
    #: reader would be a second precedence rule, and the first edit to one of
    #: them would make the two disagree. The 从题目建项目 screen needs it to say
    #: where the settings came from rather than only handing over the result.
    teaching_source: str | None = None

    @property
    def compute_credits(self) -> float:
        """Credits to grant, or 0. Bools are rejected on purpose — `True` is an
        int in Python and would silently become a one-credit grant."""
        value = self.resource_pack.get("compute_credits")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return 0.0
        return float(value) if value > 0 else 0.0

    @property
    def pack_prompt(self) -> str | None:
        """The prompt a 资源包 still carries (``resource_pack["prompt"]``), which
        project creation appends to the default teammate's persona. Read until
        the contract migration after b7d4e1a9c2f3 moves it into the guidance's
        requirements; that migration's PR removes this."""
        return _text(self.resource_pack.get("prompt"))

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


def _level_value(source: Any, key: str) -> Any:
    """One key's value at one level: a row's attribute, or a dict's item.

    The levels are spelled two ways on purpose — a 空间/项目集/项目 is a row
    (attributes), a 赛题 override and a project's ``settings`` are free-form
    dicts. Reading them through one function keeps the precedence in the loop
    below rather than in four nearly-identical lines. Missing is ``None`` —
    "this level did not say", never an empty value.
    """
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _resolve_teaching(
    *,
    space: Any | None,
    category: Any | None,
    task: Any | None,
    project: Any | None,
) -> tuple[Teaching, str | None]:
    """How the course wants this project's AI teammates to help, and where
    that came from: ``"space"``, ``"task"``, ``"both"``, ``"project"``, or
    None when nobody said anything.

    The space's settings and the 题目's are both in force (`Teaching.combine`).
    A project's own settings, when it has any, replace them whole. The 项目集
    level is no longer read: its settings moved onto its 题目 when it went.
    """
    del category  # no longer a level
    on_space = Teaching.from_json(_level_value(space, "teaching"))
    on_task = Teaching.from_json(
        _level_value(getattr(task, "protocol_override", None) or {}, "teaching")
    )
    on_project = Teaching.from_json(
        _level_value(_project_override(project), "teaching")
    )
    if not on_project.is_empty:
        return on_project, "project"
    if on_space.is_empty and on_task.is_empty:
        return Teaching(), None
    source = (
        "both"
        if not on_space.is_empty and not on_task.is_empty
        else "space"
        if not on_space.is_empty
        else "task"
    )
    return on_space.combine(on_task), source


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
    *,
    space: Any | None = None,
    category: Any | None = None,
    task: Any | None = None,
    project: Any | None = None,
) -> Protocol:
    """The terms in force for one project: four levels, most specific last.

    空间 → 项目集 → 题目 (`protocol_override`) → 项目 (`settings`). Every key
    takes the same trip, `shell` and `teaching` included: the levels are a
    property of the protocol, not of any one field in it, so a field added here
    is overridable at every level the day it is added rather than when someone
    remembers to wire it up again.

    The 空间 is the OUTERMOST level — a default set for the whole board. Only
    `teaching` lives there today (a 空间 carries no resource pack or 壳), and
    the loop below reads whatever keys it does carry, so the level is wired once
    rather than specially for one field.

    Each level replaces a key WHOLE — no deep merge, for the reason the 分组
    placement was chosen: a half-inherited resource pack is harder to reason
    about than either source alone. `teaching` is the exception: the space's
    and the 题目's are both in force — see `_resolve_teaching`.

    Every argument is optional so callers do not have to branch: a 题目 with no
    分组, or a project with no 题目 at all, resolves to an empty protocol — which
    is the 项目自治 default (spec §4), not an error.
    """
    values: dict[str, Any] = {}
    for source in (
        space,
        category,
        getattr(task, "protocol_override", None) or {},
        _project_override(project),
    ):
        for key in _OVERRIDABLE:
            if key == "teaching":
                # Not read here: an empty level means "inherit", which the
                # presence rule below cannot express. `_resolve_teaching` owns it.
                continue
            value = _level_value(source, key)
            if key == "default_role" and not (isinstance(value, str) and value.strip()):
                # A blank default teammate is a level saying nothing — the
                # page's 「继承」 wrote it — so the level above stands.
                continue
            if value is not None:
                values[key] = value
    teaching, teaching_source = _resolve_teaching(
        space=space, category=category, task=task, project=project
    )
    return Protocol(
        resource_pack=dict(values.get("resource_pack") or {}),
        conditions=list(values.get("conditions") or []),
        default_role=values.get("default_role") or None,
        shell=values.get("shell") or None,
        teaching=teaching,
        teaching_source=teaching_source,
    )
