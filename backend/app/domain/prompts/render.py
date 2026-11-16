"""Rendering a profile: the system prompt, the opening, the notice after every
message.

Rendering checks what it renders: every tool a selected piece names must be one
the profile holds (`ProfileError`), so a rule can never send a session to a tool
it does not have. `check` runs the same check over every piece a profile could
select, for the tests and the docs generator.

**教学配置的生效语义 (#8d772257)。** 改一次课程配置，三种项目分别什么时候看见
它——这是设计决定，不是实现细节：

1. **新建的项目**：建出来就带最新配置。建项目那一轮本来就要组装一次 prompt，
   读到的就是当时项目集里的那一份，没有缓存层要等。
2. **已有项目的新会话**：启动时读到最新配置。prompt 每一轮都从库里重新组装
   （`turn/intake/preparation/streaming.py` 的 `converse` 每次都重新 resolve），所以新开的会话拿到的一定是
   此刻的配置。
3. **运行中的会话**：**保持它启动时的那一份，直到下一次冷启动。** 这是 Claude Code
   的事实：harness 用 `--append-system-prompt-file` 把 prompt 交给它，而它**在启动时
   读一次**那个文件（`claude_code/session_launch.py` 的模块注释）。往一个已经跑起来、
   上下文里全是第 3 周内容的会话里塞第 4 周的范围，比让它按第 3 周做完这一轮更糟。
   要立刻生效就重开会话。

逐轮的用户消息里**不放**教学上下文，这是第 3 条的实现保证：它只走开场这一条路
（不在 `TRACKED_SECTIONS` 里），没有第二条路能让它在会话中途变脸。
"""

from collections.abc import Callable, Mapping

from app.domain.prompts.notices import NoticeType, platform_prompt
from app.domain.prompts.opening import SessionOpening, build_session_opening
from app.domain.prompts.profile import (
    BASE,
    CARRIED,
    SKILLS,
    Facts,
    Include,
    Kind,
    Profile,
    ProfileError,
    holds,
)
from app.domain.prompts.profiles import PROFILES
from app.domain.prompts.text import in_language, includes, piece, tools_in

#: A skill's body by its name (`app.domain.agent.skills.load_skill`).
SkillReader = Callable[[str], str]

#: How skill bodies are joined where a system prompt says `SKILLS`.
SKILL_JOIN = "\n\n---\n\n"


def profile(kind: Kind) -> Profile:
    return PROFILES[kind]


def _checked(p: Profile, piece_id: str) -> None:
    for tool in sorted(tools_in(piece_id)):
        if not holds(p.tools, tool):
            raise ProfileError(p.kind, piece_id, tool)


def check(kind: Kind) -> None:
    """Raise `ProfileError` if any piece the profile could render names a tool
    it does not hold, whatever the facts."""
    p = PROFILES[kind]
    for inc in p.system:
        if inc.piece not in (BASE, SKILLS):
            _checked(p, inc.piece)
    for lang in ("zh", "en"):
        for base in (*((p.notice,) if p.notice else ()), *p.reminders):
            _checked(p, f"{base}.{lang}")


def _present(inc: Include, facts: Facts, values: Mapping[str, object]) -> bool:
    return inc.when(facts) and all(values.get(name) for name in inc.needs)


def selected(
    kind: Kind, facts: Facts, values: Mapping[str, object] | None = None
) -> tuple[str, ...]:
    """The pieces a session of ``kind`` reads in its system prompt with these
    facts, included pieces too, in reading order. `BASE` and `SKILLS` stand
    for what the caller passes."""
    p = PROFILES[kind]
    given: dict[str, object] = {"name": facts.name, "role": facts.role}
    given.update(values or {})
    out: list[str] = []
    for inc in p.system:
        if _present(inc, facts, given):
            out.extend(
                (inc.piece,) if inc.piece in (BASE, SKILLS) else includes(inc.piece)
            )
    return tuple(out)


def render_system(
    kind: Kind,
    facts: Facts,
    *,
    skill: SkillReader | None = None,
    base: str = "",
    carried: str = "",
    values: Mapping[str, object] | None = None,
) -> str:
    """The system prompt of a session of ``kind``.

    ``base`` is the deployment's opening paragraph (`settings.agent_system_prompt`),
    ``carried`` the skill bodies the teammate brings (`context.carried_skills`),
    ``values`` the slots a profile's pieces take beyond the facts. Nothing here
    may change within a session: a changed byte voids the cached prefix from
    there on, history included."""
    p = PROFILES[kind]
    read = skill or _no_skills
    given: dict[str, object] = {"name": facts.name, "role": facts.role}
    given.update(values or {})
    parts: list[str] = []
    for inc in p.system:
        if not _present(inc, facts, given):
            continue
        if inc.piece == BASE:
            parts.append(base)
        elif inc.piece == SKILLS:
            bodies = [carried if name == CARRIED else read(name) for name in p.skills]
            joined = SKILL_JOIN.join(b for b in bodies if b)
            if joined:
                parts.append(joined)
        else:
            _checked(p, inc.piece)
            slots = {**given, **inc.slots}
            slots.update({k: read(v) for k, v in inc.skill_slots.items()})
            parts.append(piece(inc.piece, **slots))
    return "\n\n".join(parts)


def _no_skills(name: str) -> str:
    raise LookupError(
        f"rendering needs the skill {name}; pass skill="
    )  # i18n-exempt: a caller passed no skill reader, for a developer


def render_opening(kind: Kind, **sections: object) -> SessionOpening:
    """The opening of a session of ``kind``: the sections its profile is told,
    from what the caller read (`opening.build_session_opening`'s arguments)."""
    allowed = PROFILES[kind].opening
    kept = {
        key: value
        for key, value in sections.items()
        if _SECTION_OF.get(key, key) in allowed
    }
    return build_session_opening(**kept)  # type: ignore[arg-type]


#: The section each of `build_session_opening`'s arguments opens; an argument
#: that only shapes another section goes with it.
_SECTION_OF = {
    "overview_doc": "overview",
    "overview_doc_id": "overview",
    "keeps_memory": "memory",
}


def render_notice(kind: Kind, content: str, language: str | None = None) -> str:
    """What a session of ``kind`` reads: ``content``, then the notice its
    profile says after every message, in the reader's language."""
    p = PROFILES[kind]
    if p.notice is None:
        return content
    _checked(p, f"{p.notice}.{'en' if language == 'en' else 'zh'}")
    return (
        content
        + "\n\n"
        + platform_prompt(in_language(p.notice, language), NoticeType.contract)
    )
