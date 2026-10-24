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


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _texts(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(t for v in value if (t := _text(v)) is not None))


def _stripped(pack):
    """The 资源包 without the moved keys, or None when it has none of them."""
    if not isinstance(pack, dict) or not any(k in pack for k in _MOVED):
        return None
    return {k: v for k, v in pack.items() if k not in _MOVED}


def _with_pack(teaching, pack) -> dict | None:
    """``teaching`` with ``pack``'s prompt and skills added, or None when the
    pack carries neither."""
    pack = pack if isinstance(pack, dict) else {}
    prompt = _text(pack.get("prompt"))
    skills = _texts(pack.get("skills"))
    if prompt is None and not skills:
        return None
    out = dict(teaching) if isinstance(teaching, dict) else {}
    if prompt is not None:
        said = _text(out.get("system_prompt"))
        if said is None:
            out["system_prompt"] = prompt
        elif prompt not in said:
            out["system_prompt"] = f"{said}\n\n{prompt}"
    if skills:
        out["skills"] = list(dict.fromkeys([*_texts(out.get("skills")), *skills]))
    return out


def upgrade() -> None:
    bind = op.get_bind()

    packs = {
        category_id: pack
        for category_id, pack in bind.execute(
            sa.text("SELECT id, resource_pack FROM space_categories")
        )
    }

    for task_id, category_id, override in bind.execute(
        sa.text("SELECT id, category_id, protocol_override FROM task")
    ):
        override = dict(override) if isinstance(override, dict) else {}
        own = override.get("resource_pack")
        pack = own if own is not None else packs.get(category_id)
        teaching = _with_pack(override.get("teaching"), pack)
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


def downgrade() -> None:
    """Leaves the moved prompt and skills in the guidance.

    The prompt cannot be told back apart from the requirements it was added to,
    and the previous release reads a guidance with ``skills`` fine (the key is
    ignored), so nothing a teacher wrote is lost by staying.
    """
