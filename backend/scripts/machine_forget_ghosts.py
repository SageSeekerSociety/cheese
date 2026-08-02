"""Drop machine rows MicroCloud no longer knows about.

A machine that reached `running` was never re-checked, so a destroyed one keeps
reading as healthy here forever — and `provision()` counts those rows against
`microcloud_max_machines_per_project`, which means a project whose machines are
all gone can never get another one. PR #160 fixes that in the service; this is
the one-off for a database that already has the ghosts.

Only rows MicroCloud answers 404 for are dropped. An unreachable MicroCloud (a
timeout, a 500) proves nothing about the machine and leaves every row alone —
deleting on a blip would destroy the platform's only record of a live machine.

  docker exec -w /app -e PYTHONPATH=/app cheese-backend-1 \
      python machine_forget_ghosts.py [--apply]

Without --apply it only reports.
"""

import asyncio
import sys

from sqlalchemy import select

from app.core.db import async_session_factory

# The ORM delete configures mappers, and ProjectMachine.project_id points at a
# table declared elsewhere — without this import the delete fails on an
# unresolvable foreign key while the read-only report works fine.
from app.domain.project import models as _project_models  # noqa: F401
from app.domain.machine.microcloud import MicroCloudClient
from app.domain.machine.models import ProjectMachine
from app.domain.machine.repositories import ProjectMachineRepository


async def main() -> int:
    apply = "--apply" in sys.argv
    client = MicroCloudClient()
    async with async_session_factory() as session:
        repo = ProjectMachineRepository(session)
        rows = list((await session.execute(select(ProjectMachine))).scalars())
        gone: list[object] = []
        for row in rows:
            try:
                remote = await client.get_machine(row.machine_id)
            except Exception as exc:  # noqa: BLE001 - unreachable proves nothing
                print(f"{row.hostname} ({row.machine_id}): UNREACHABLE {exc} — kept")
                continue
            if remote is None:
                print(f"{row.hostname} ({row.machine_id}): GONE (404), db={row.status}")
                gone.append(row)
            else:
                print(
                    f"{row.hostname} ({row.machine_id}): alive"
                    f" status={remote.get('status')} aiMode={remote.get('aiMode')}"
                )
        if not apply:
            print(f"\n{len(gone)} ghost row(s); re-run with --apply to drop them")
            return 0
        for row in gone:
            await repo.delete(row)
        await session.commit()
        print(f"\ndropped {len(gone)} ghost row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
