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
``skills``.

For every 题目, the 资源包 it was reading — its own override when it has one,
else its 项目集's (a whole-key replacement, as ``protocol.resolve`` reads it)
— is moved into its guidance; then ``prompt`` and ``skills`` are removed from
every 资源包, so only ``compute_credits`` is left.

What every teammate reads stays what it was:

- A 题目 that had no guidance of its own read its 空间's alone, learning goals
  and private guidance included. Once the move gives it guidance of its own,
  ``Teaching.combine`` takes those two from the 题目, so the 空间's are copied
  onto it with the move.
- A project built from such a 题目 had the prompt appended to its default
  teammate's persona (``configuration.body``, joined with a blank line). The
  guidance now says it on every turn, so it is taken out of the persona rather
  than read twice. A persona that no longer holds the text (edited since, or a
  prompt the 题目 changed afterwards) is left as it is.

Data only, on a few dozen rows at most, so one pass in Python. Idempotent: a
second run finds no ``prompt`` or ``skills`` in any 资源包 and changes nothing.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d4e1a9c2f3"
down_revision: str | Sequence[str] | None = "e6b2d9f4a173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVED = ("prompt", "skills")

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


def _stripped(pack):
    """The 资源包 without the moved keys, or None when it has none of them."""
    if not isinstance(pack, dict) or not any(k in pack for k in _MOVED):
        return None
    return {k: v for k, v in pack.items() if k not in _MOVED}


def _with_pack(teaching, pack, space_teaching) -> dict | None:
    """``teaching`` with ``pack``'s prompt and skills added, or None when the
    pack carries neither. Guidance that said nothing before takes the 空间's
    learning goals and private guidance, which it was reading until now."""
    pack = pack if isinstance(pack, dict) else {}
    prompt = _text(pack.get("prompt"))
    skills = _texts(pack.get("skills"))
    if prompt is None and not skills:
        return None
    out = dict(teaching) if isinstance(teaching, dict) else {}
    if _blank(teaching) and isinstance(space_teaching, dict):
        goals = _texts(space_teaching.get("learning_goals"))
        private = _text(space_teaching.get("private_prompt"))
        if goals:
            out["learning_goals"] = goals
        if private is not None:
            out["private_prompt"] = space_teaching["private_prompt"]
    if prompt is not None:
        said = _text(out.get("system_prompt"))
        if said is None:
            out["system_prompt"] = prompt
        elif prompt not in said:
            out["system_prompt"] = f"{said}\n\n{prompt}"
    if skills:
        out["skills"] = list(dict.fromkeys([*_texts(out.get("skills")), *skills]))
    return out


def _persona_without(body, prompt):
    """The persona with the prompt the 资源包 appended taken out, or None when
    it does not hold it."""
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

    #: The prompt each 题目's 资源包 carried, raw, as it was appended.
    prompts: dict[int, str] = {}
    for task_id, category_id, space_id, override in bind.execute(
        sa.text("SELECT id, category_id, space_id, protocol_override FROM task")
    ):
        override = dict(override) if isinstance(override, dict) else {}
        own = override.get("resource_pack")
        pack = own if own is not None else packs.get(category_id)
        if isinstance(pack, dict) and _text(pack.get("prompt")) is not None:
            prompts[task_id] = pack["prompt"]
        teaching = _with_pack(override.get("teaching"), pack, spaces.get(space_id))
        stripped = _stripped(own)
        if teaching is None and stripped is None:
            continue
        if teaching is not None:
            override["teaching"] = teaching
        if stripped is not None:
            override["resource_pack"] = stripped
        bind.execute(
            sa.text(
                "UPDATE task SET protocol_override = CAST(:o AS json) WHERE id = :id"
            ),
            {"o": json.dumps(override, ensure_ascii=False), "id": task_id},
        )

    for category_id, pack in packs.items():
        stripped = _stripped(pack)
        if stripped is not None:
            bind.execute(
                sa.text(
                    "UPDATE space_categories SET resource_pack = CAST(:p AS json)"
                    " WHERE id = :id"
                ),
                {"p": json.dumps(stripped, ensure_ascii=False), "id": category_id},
            )

    if not prompts:
        return
    for instance_id, task_id, configuration in bind.execute(
        sa.text(
            "SELECT a.id, p.external_task_id, a.configuration"
            " FROM projects p JOIN agent_instances a"
            " ON a.id = p.default_agent_instance_id"
            " WHERE p.external_task_id IS NOT NULL"
        )
    ):
        if task_id not in prompts or not isinstance(configuration, dict):
            continue
        body = _persona_without(configuration.get("body"), prompts[task_id])
        if body is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE agent_instances SET configuration = CAST(:c AS json)"
                " WHERE id = :id"
            ),
            {
                "c": json.dumps({**configuration, "body": body}, ensure_ascii=False),
                "id": instance_id,
            },
        )


def downgrade() -> None:
    """Leaves the moved prompt and skills in the guidance.

    The prompt cannot be told back apart from the requirements it was added to,
    and the previous release reads a guidance with ``skills`` fine (the key is
    ignored), so nothing a teacher wrote is lost by staying.
    """
