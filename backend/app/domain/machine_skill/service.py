"""The machine_skill index, read and written from the platform side.

A scan hands :meth:`MachineSkillService.record_scan` what the machine just said
and this writes the four states ``machine_skill/models.py`` describes: an
adopted row whose digest moved is ``changed``, one the scan did not find is
``missing``, a plain find is ``seen``, and a ``seen`` row nobody confirmed that
the scan no longer finds is deleted. A person's :meth:`set_adoption` is the only
other write.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.machine_skill.models import MachineSkillRow

#: ``seen`` is the default; the other three are a scan's reading of a row.
STATE_SEEN = "seen"
STATE_ADOPTED = "adopted"
STATE_CHANGED = "changed"
STATE_MISSING = "missing"


class MachineSkillService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, device_id: str) -> list[MachineSkillRow]:
        result = await self._session.execute(
            select(MachineSkillRow)
            .where(MachineSkillRow.device_id == device_id)
            .order_by(MachineSkillRow.name)
        )
        return list(result.scalars())

    async def find(self, device_id: str, name: str) -> MachineSkillRow | None:
        """The row for one name, or ``None`` — the reading side wants the absence
        as a fact (a name a teammate carries that this machine does not have),
        not as an error."""
        return await self._session.get(MachineSkillRow, (device_id, name))

    async def get(self, device_id: str, name: str) -> MachineSkillRow:
        row = await self.find(device_id, name)
        if row is None:
            raise NotFoundError(say("skillNotFound"))
        return row

    async def record_scan(
        self, device_id: str, found: list[dict]
    ) -> list[MachineSkillRow]:
        """Write one scan's result, then return the index it left behind."""
        rows = {row.name: row for row in await self.list(device_id)}
        seen: set[str] = set()
        for skill in found:
            name = str(skill["name"])
            seen.add(name)
            row = rows.get(name)
            if row is None:
                self._session.add(
                    MachineSkillRow(
                        device_id=device_id,
                        name=name,
                        description=str(skill.get("description") or ""),
                        source_root=str(skill["source_root"]),
                        digest=str(skill["digest"]),
                        state=STATE_SEEN,
                    )
                )
                continue
            adopted = row.confirmed_by is not None
            row.description = str(skill.get("description") or "")
            row.source_root = str(skill["source_root"])
            if adopted and row.digest != str(skill["digest"]):
                row.state = STATE_CHANGED
            elif adopted:
                row.state = STATE_ADOPTED
            else:
                row.state = STATE_SEEN
            row.digest = str(skill["digest"])
        for name, row in rows.items():
            if name in seen:
                continue
            if row.confirmed_by is not None:
                row.state = STATE_MISSING
            else:
                await self._session.delete(row)
        await self._session.flush()
        return await self.list(device_id)

    async def set_adoption(
        self, device_id: str, name: str, *, adopted: bool, by: int | str
    ) -> MachineSkillRow:
        """A person's decision about one skill: adopt it, or take it back.

        Adopting is what lets a teammate pick the skill up. Un-adopting clears the
        confirmation and drops an adopted row back to ``seen``; a row the scan has
        stopped finding stays ``missing`` (the next scan deletes it by the same
        rule: nobody confirmed it and it is not on the machine).
        """
        row = await self.get(device_id, name)
        if adopted:
            row.confirmed_by = str(by)
            row.state = STATE_ADOPTED
        else:
            row.confirmed_by = None
            if row.state != STATE_MISSING:
                row.state = STATE_SEEN
        await self._session.flush()
        return row
