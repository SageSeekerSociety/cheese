"""A 资源包's skills are given by the 题目's AI guidance too

Revision ID: b7d4e1a9c2f3
Revises: e6b2d9f4a173
Create Date: 2026-10-24

A 资源包 (``resource_pack``, on a 项目集 and overridable on a 题目) could carry
a ``prompt`` and a list of ``skills`` besides its compute. They were copied
into the default teammate once, when a project was built, so a teammate added
later never got them. This release reads skills from the 题目's guidance
(``protocol_override -> teaching -> skills``) on every turn, for every
teammate, and reads neither key of the 资源包.

This is the expand half of the move, and it only adds what this release reads:
for every 题目, the skills of the 资源包 it was reading — its own override when
it has one, else its 项目集's (a whole-key replacement, as ``protocol.resolve``
reads it) — are added to its guidance's ``skills``. The previous release serves
while this runs and reads none of that key, so nothing it reads changes: the
资源包 keys, the guidance's other fields and every teammate's configuration are
left exactly as they are. Moving the prompt into the requirements, taking the
copies out of existing default teammates and removing the 资源包 keys is the
contract half, a later migration once this release is deployed.

Data only, on a few dozen rows at most, so one pass in Python. Idempotent: a
second run finds every skill already listed.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d4e1a9c2f3"
down_revision: str | Sequence[str] | None = "e6b2d9f4a173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _texts(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(v.strip() for v in value if isinstance(v, str) and v.strip())
    )


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
        skills = _texts(pack.get("skills")) if isinstance(pack, dict) else []
        if not skills:
            continue
        teaching = override.get("teaching")
        teaching = dict(teaching) if isinstance(teaching, dict) else {}
        given = _texts(teaching.get("skills"))
        merged = list(dict.fromkeys([*given, *skills]))
        if merged == given:
            continue
        teaching["skills"] = merged
        override["teaching"] = teaching
        bind.execute(
            sa.text(
                "UPDATE task SET protocol_override = CAST(:o AS json) WHERE id = :id"
            ),
            {"o": json.dumps(override, ensure_ascii=False), "id": task_id},
        )


def downgrade() -> None:
    """Leaves the added skills in the guidance: the previous release does not
    read that key, and the 资源包 still carries them for it."""
