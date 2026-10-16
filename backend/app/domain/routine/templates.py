"""A project's saved form presets: the 「存成模板」 half of the compose form.

A template is what the form held, kept to fill the next one from — not a rule.
Nothing runs off it and nothing reports back to it, so a template has no owner,
no state and no run history, and it belongs to the project rather than to the
room or to the person who typed it: whoever may read the project reads the same
list, and may add to it or take from it.

What it does share with a rule is the two fields that are easy to get wrong —
``trigger`` and ``spec`` — and those go through the same check a rule's do
(:func:`app.domain.routine.service.validate_spec`), so a template no rule could
be made from is refused when it is saved rather than when it is picked.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.routine.models import RoutineTemplate
from app.domain.routine.service import name_of, validate_spec

#: What the clock in ``spec`` is checked against. A template carries no time
#: zone of its own — the person picking it fills the form under theirs — and
#: this one only has to be a zone that exists: ``validate_spec`` reads it to
#: check that 「09:00」 and 「每周一」 are written down properly, not to work out
#: when anything runs.
CHECK_TZ = "Asia/Shanghai"


class RoutineTemplateService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list(self, project_id: uuid.UUID) -> list[RoutineTemplate]:
        """这个项目里存下来的模板，最近存的在最前面（和它还在浏览器里时一样）。"""
        rows = await self._session.scalars(
            select(RoutineTemplate)
            .where(RoutineTemplate.project_id == project_id)
            .order_by(RoutineTemplate.created_at.desc())
        )
        return list(rows)

    async def get(self, template_id: uuid.UUID) -> RoutineTemplate:
        row = await self._session.get(RoutineTemplate, template_id)
        if row is None:
            raise NotFoundError(say("routineTemplateNotFound"))
        return row

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        goal: str,
        note: str,
        trigger: str,
        spec: dict,
    ) -> RoutineTemplate:
        """存下一条。名字没写就从目标的第一句里取，和一条规则一样（`name_of`）。"""
        goal = (goal or "").strip()
        if not goal:
            raise ValidationError(say("routineTemplateGoalRequired"))
        row = RoutineTemplate(
            id=uuid.uuid4(),
            project_id=project_id,
            title=name_of(title, goal),
            goal=goal,
            note=(note or "").strip(),
            trigger=trigger,
            spec=validate_spec(trigger, spec, CHECK_TZ),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def delete(self, row: RoutineTemplate) -> None:
        await self._session.delete(row)
