"""AI teammate guidance: two levels, a way of helping, requirements in words

Revision ID: e5f1a9c3d7b2
Revises: 1c646deda360
Create Date: 2026-10-21

Guidance (``teaching``) was a template with three variables (the week, what
the week covers, what not to use yet) that 空间, 项目集 and 题目 each replaced
whole. Now a 空间 and its 题目 are both in force, each with a way of helping
(``mode``) and requirements written out, and the 项目集 level is gone.

So, in every stored config (``space.teaching``, ``space_categories.teaching``
and ``task.protocol_override -> teaching``):

- the template is filled in with its variables, and a variable the template did
  not use is said in a sentence after it — nothing a teacher wrote is dropped;
- the week, the topics and the things to avoid are removed;
- a 项目集's config moves onto each of its 题目 that has none of its own (that
  is what those 题目 were reading), and the 项目集's is cleared.

Data only. Guidance is configured on a few dozen rows at most, so one pass in
Python; it is idempotent (a config with none of the old keys is left as it is).
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f1a9c3d7b2"
down_revision: str | Sequence[str] | None = "1c646deda360"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("current_week", "allowed_topics", "avoid_in_code")


def _texts(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _converted(raw):
    """The config in the new shape, or None when there is nothing to change.
    The template filling is copied from `Teaching.fill` as it was."""
    if not isinstance(raw, dict) or not any(k in raw for k in _OLD):
        return None
    template = (
        raw.get("system_prompt") if isinstance(raw.get("system_prompt"), str) else ""
    )
    week = raw.get("current_week")
    week = (
        week
        if isinstance(week, int) and not isinstance(week, bool) and week >= 0
        else None
    )
    topics = _texts(raw.get("allowed_topics"))
    avoid = _texts(raw.get("avoid_in_code"))
    text = template
    for token, value in (
        ("{current_week}", str(week) if week is not None else "（未指定）"),
        ("{allowed_topics}", "、".join(topics) or "（未指定）"),
        ("{avoid_in_code}", "、".join(avoid) or "（未指定）"),
    ):
        text = text.replace(token, value)
    said = [text.strip()] if text.strip() else []
    if week is not None and "{current_week}" not in template:
        said.append(f"当前是第 {week} 周。")
    if topics and "{allowed_topics}" not in template:
        said.append("现阶段的内容：" + "、".join(topics) + "。")
    if avoid and "{avoid_in_code}" not in template:
        said.append("暂时不要用到：" + "、".join(avoid) + "。")
    out = {k: v for k, v in raw.items() if k not in _OLD}
    out["system_prompt"] = "\n".join(said) or None
    return out


def _blank(raw) -> bool:
    if not isinstance(raw, dict):
        return True
    return not (
        raw.get("mode")
        or (isinstance(raw.get("system_prompt"), str) and raw["system_prompt"].strip())
        or raw.get("material_ids")
        or raw.get("document_ids")
    )


def upgrade() -> None:
    bind = op.get_bind()

    for space_id, raw in bind.execute(sa.text("SELECT id, teaching FROM space")):
        new = _converted(raw)
        if new is not None:
            bind.execute(
                sa.text("UPDATE space SET teaching = CAST(:t AS json) WHERE id = :id"),
                {"t": json.dumps(new, ensure_ascii=False), "id": space_id},
            )

    tasks = {
        task_id: (category_id, override if isinstance(override, dict) else None)
        for task_id, category_id, override in bind.execute(
            sa.text("SELECT id, category_id, protocol_override FROM task")
        )
    }
    changed: dict[int, dict] = {}
    for task_id, (_, override) in tasks.items():
        new = _converted((override or {}).get("teaching"))
        if new is not None:
            changed[task_id] = {**(override or {}), "teaching": new}

    for category_id, raw in bind.execute(
        sa.text("SELECT id, teaching FROM space_categories")
    ):
        config = _converted(raw) or raw
        if _blank(config):
            continue
        for task_id, (in_category, override) in tasks.items():
            if in_category != category_id:
                continue
            current = changed.get(task_id, override or {})
            if _blank(current.get("teaching")):
                changed[task_id] = {**current, "teaching": config}
        bind.execute(
            sa.text("UPDATE space_categories SET teaching = '{}' WHERE id = :id"),
            {"id": category_id},
        )

    for task_id, override in changed.items():
        bind.execute(
            sa.text(
                "UPDATE task SET protocol_override = CAST(:o AS json) WHERE id = :id"
            ),
            {"o": json.dumps(override, ensure_ascii=False), "id": task_id},
        )


def downgrade() -> None:
    """Leaves the folded text and the moved guidance in place.

    The old fields cannot be told back apart from the text they were folded
    into, and the new shape reads fine as the old (an unknown `mode` is ignored).
    """
