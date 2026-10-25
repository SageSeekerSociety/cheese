"""A 资源包's prompt and skills move into the 题目's AI guidance

Revision ID: b7d4e1a9c2f3
Revises: e6b2d9f4a173
Create Date: 2026-10-24

A 资源包 (``resource_pack``, on a 项目集 and overridable on a 题目) could carry
a ``prompt`` and a list of ``skills`` besides its compute. They were copied
into the default teammate once, when a project was built; a teammate added
later never got them, and a 题目 that changed them reached no project already
running. They now live in the 题目's guidance (``protocol_override ->
teaching``), which every turn of every teammate reads: the prompt is added to
the guidance's requirements (``system_prompt``), the skills to its
``skills``. For every 题目 the 资源包 it was reading is moved — its own
override when it has one, else its 项目集's (a whole-key replacement, as
``protocol.resolve`` reads it).

What every teammate reads stays what it was:

- A 题目 that had no guidance of its own read its 空间's alone, learning goals
  and private guidance included. Once the move gives it guidance of its own,
  ``Teaching.combine`` takes those two from the 题目, so the 空间's are copied
  onto it with the move.
- Guidance with learning goals and no requirements in force was given the
  platform's default teaching requirements (``DEFAULT_TEACHING_PROMPT``). The
  moved prompt is a requirement, which would switch that default off, so the
  default's text goes in front of it.
- A project built from such a 题目 had the prompt appended to its default
  teammate's persona (``configuration.body``, joined with a blank line) and
  the skills merged into its ``configuration.skills``. The guidance now gives
  both on every turn, so they come out of the teammate rather than being had
  twice. A persona that no longer ends with the text is left as it is, and so
  is a project with guidance of its own (``settings.teaching``), which
  replaces the 题目's whole.
- A 题目's own ``default_role`` of ``""`` was written by the page's 「继承」,
  which meant "use the level above"; ``""`` now clears the key, and the stored
  ones are cleared the same way.

**The 资源包 keys stay.** The previous release serves while this runs and
reads ``prompt`` and ``skills`` from the 资源包 when it builds a project; they
are removed by a later migration, after this release is deployed. This
release reads neither.

Data only, on a few dozen rows at most, so one pass in Python. Idempotent: a
second run finds every prompt already in the requirements, every skill already
listed and every persona already without the prompt.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d4e1a9c2f3"
down_revision: str | Sequence[str] | None = "e6b2d9f4a173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The keys that make a guidance say something — `Teaching.is_empty` as it is
#: at this revision, copied rather than imported.
_SAYS = (
    "mode",
    "system_prompt",
    "learning_goals",
    "private_prompt",
    "material_ids",
    "document_ids",
    "skills",
)

#: `app.domain.agent.harness.opening.DEFAULT_TEACHING_PROMPT` at this revision,
#: copied rather than imported: the requirements guidance with learning goals
#: and none of its own is given.
DEFAULT_TEACHING_PROMPT = """先弄清他卡在哪一步，再决定怎么帮。
不要直接给出答案，让他自己做出来。

用问题带着他走：先问「你观察到了什么」「试过什么」，
再让他自己说出判断，最后才给方向。

一次只推进一步，一次只问一个问题；他跟上了再往下。

他做错时不要直接纠正，用一个问题让他自己发现；
讲错在哪时说清「为什么会错」和「怎么才能自己看出来」，
不只给正确写法。

他要答案要得再急，也别把答案交出去：
说明给了答案对他没帮助，然后换一个更小的提示接着陪他做。

关键的几行让他亲手写：改动里涉及判断和选择的地方，
把决定留给他——你指出哪里要想、答他的疑问，
那一小段代码让他自己敲出来；重复的力气活你来做。

能演示就别写长文：讲一条执行过程或一个概念的运转方式时，
用 cheese show 把可交互的图示、变量追踪做出来让他自己拨着看，
胜过大段文字；简单的问题就用简单文字回答，不要为了花哨生成界面。"""


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _texts(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(t for v in value if (t := _text(v)) is not None))


def _blank(teaching) -> bool:
    if not isinstance(teaching, dict):
        return True
    return not any(
        _text(teaching.get(k)) if isinstance(teaching.get(k), str) else teaching.get(k)
        for k in _SAYS
    )


def _pack_parts(pack) -> tuple[str | None, list[str]]:
    pack = pack if isinstance(pack, dict) else {}
    return _text(pack.get("prompt")), _texts(pack.get("skills"))


def _with_pack(teaching, pack, space_teaching) -> dict | None:
    """``teaching`` with ``pack``'s prompt and skills added, or None when the
    pack carries neither — read the way the guidance in force before it was."""
    prompt, skills = _pack_parts(pack)
    if prompt is None and not skills:
        return None
    space = space_teaching if isinstance(space_teaching, dict) else {}
    out = dict(teaching) if isinstance(teaching, dict) else {}
    if _blank(teaching):
        # It read the 空间's alone; what `combine` takes from the 题目 comes
        # along so it still reads the 空间's.
        goals = _texts(space.get("learning_goals"))
        if goals:
            out["learning_goals"] = goals
        if _text(space.get("private_prompt")) is not None:
            out["private_prompt"] = space["private_prompt"]
    if prompt is not None:
        said = _text(out.get("system_prompt"))
        if said is None:
            in_force = _text(space.get("system_prompt"))
            # Goals and no requirements anywhere: the default requirements were
            # in force, and stay so in front of the moved prompt.
            out["system_prompt"] = (
                f"{DEFAULT_TEACHING_PROMPT}\n\n{prompt}"
                if in_force is None and _texts(out.get("learning_goals"))
                else prompt
            )
        elif prompt not in said:
            out["system_prompt"] = f"{said}\n\n{prompt}"
    if skills:
        out["skills"] = list(dict.fromkeys([*_texts(out.get("skills")), *skills]))
    return out


def _persona_without(body, prompt):
    """The persona with the prompt the 资源包 appended taken out, or None when
    it does not end with it."""
    if not isinstance(body, str) or not isinstance(prompt, str) or not prompt:
        return None
    if body == prompt:
        return ""
    if body.endswith("\n\n" + prompt):
        return body[: -len("\n\n" + prompt)]
    return None


def upgrade() -> None:
    bind = op.get_bind()

    packs = {
        category_id: pack
        for category_id, pack in bind.execute(
            sa.text("SELECT id, resource_pack FROM space_categories")
        )
    }
    spaces = {
        space_id: teaching
        for space_id, teaching in bind.execute(
            sa.text("SELECT id, teaching FROM space")
        )
    }

    #: What each 题目's 资源包 carried: its raw prompt, as it was appended, and
    #: its skills.
    carried: dict[int, tuple[str | None, list[str]]] = {}
    for task_id, category_id, space_id, override in bind.execute(
        sa.text("SELECT id, category_id, space_id, protocol_override FROM task")
    ):
        override = dict(override) if isinstance(override, dict) else {}
        own = override.get("resource_pack")
        pack = own if own is not None else packs.get(category_id)
        _, skills = _pack_parts(pack)
        raw = pack.get("prompt") if isinstance(pack, dict) else None
        if _text(raw) is not None or skills:
            carried[task_id] = (raw if _text(raw) is not None else None, skills)
        teaching = _with_pack(override.get("teaching"), pack, spaces.get(space_id))
        clears_role = override.get("default_role") == ""
        if teaching is None and not clears_role:
            continue
        if teaching is not None:
            override["teaching"] = teaching
        if clears_role:
            del override["default_role"]
        bind.execute(
            sa.text(
                "UPDATE task SET protocol_override = CAST(:o AS json) WHERE id = :id"
            ),
            {"o": json.dumps(override, ensure_ascii=False), "id": task_id},
        )

    if not carried:
        return
    for instance_id, task_id, settings, configuration in bind.execute(
        sa.text(
            "SELECT a.id, p.external_task_id, p.settings, a.configuration"
            " FROM projects p JOIN agent_instances a"
            " ON a.id = p.default_agent_instance_id"
            " WHERE p.external_task_id IS NOT NULL"
        )
    ):
        if task_id not in carried or not isinstance(configuration, dict):
            continue
        # A project with guidance of its own does not read the 题目's: what its
        # teammate was given stays with it.
        if isinstance(settings, dict) and not _blank(settings.get("teaching")):
            continue
        prompt, skills = carried[task_id]
        config = dict(configuration)
        body = _persona_without(config.get("body"), prompt)
        if body is not None:
            config["body"] = body
        own = config.get("skills")
        if isinstance(own, list) and skills:
            config["skills"] = [s for s in own if s not in skills]
        if config == configuration:
            continue
        bind.execute(
            sa.text(
                "UPDATE agent_instances SET configuration = CAST(:c AS json)"
                " WHERE id = :id"
            ),
            {"c": json.dumps(config, ensure_ascii=False), "id": instance_id},
        )


def downgrade() -> None:
    """Leaves the moved prompt and skills in the guidance.

    The prompt cannot be told back apart from the requirements it was added to,
    and the previous release reads a guidance with ``skills`` fine (the key is
    ignored). The 资源包 keeps its ``prompt`` and ``skills``, so the previous
    release builds projects from it as it did.
    """
