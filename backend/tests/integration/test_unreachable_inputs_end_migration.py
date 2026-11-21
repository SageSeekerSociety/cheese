"""An unread input nothing can end stops holding its seat."""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.domain.delivery.input_holds import seats_with_unfinished_input

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_end_unread_inputs_of_works_nobody_recorded.py"
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("_mig_unreachable", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_an_input_no_exit_and_no_close_can_end_lets_its_seat_go(client):
    now = datetime.now(UTC)
    long_ago = now - timedelta(days=40)
    conversation = uuid.uuid4()
    # seat: (registered, handed to a recorded process, its work has an interval)
    seats = {
        "unreachable": (long_ago, False, False),
        "handed": (long_ago, True, False),
        "in-a-work": (long_ago, False, True),
        "just-registered": (now, False, False),
    }

    async def seed() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text(
                    "INSERT INTO conversations (id, kind, created_at) "
                    "VALUES (:id, 'personal', :now)"
                ),
                {"id": conversation, "now": now},
            )
            for seat, (registered, handed, in_a_work) in seats.items():
                work = uuid.uuid4()
                if in_a_work:
                    await s.execute(
                        text(
                            "INSERT INTO agent_turns (id, conversation_id, "
                            "continuation_id, author, content, is_resume, "
                            "resendable, started_at) VALUES (:id, :conversation, "
                            ":id, 'alice', '', false, false, :at)"
                        ),
                        {"id": work, "conversation": conversation, "at": registered},
                    )
                await s.execute(
                    text(
                        "INSERT INTO native_inputs (id, project_id, conversation_id, "
                        "recipient_handle, harness, native_session_id, input_id, "
                        "work_id, held_block_ids, released_block_ids, block_ids, "
                        "registered_at, handed_epoch) VALUES (:id, :project, "
                        ":conversation, :seat, 'claude-code', :native, :work, :work, "
                        "'[]', '[]', '[]', :at, :epoch)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "project": uuid.uuid4(),
                        "conversation": conversation,
                        "seat": seat,
                        "native": f"native-{seat}",
                        "work": work,
                        "at": registered,
                        "epoch": 3 if handed else None,
                    },
                )
            await s.commit()

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().upgrade()

    async def holding() -> set[str]:
        async with client.test_factory() as s:
            held = await seats_with_unfinished_input(
                s, [(conversation, seat) for seat in seats]
            )
        return {seat for _, seat in held}

    async def upgrade() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()

    asyncio.run(seed())
    assert asyncio.run(holding()) == set(seats)
    asyncio.run(upgrade())
    assert asyncio.run(holding()) == {"handed", "in-a-work", "just-registered"}
