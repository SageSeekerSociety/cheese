"""A notification sent before deliveries had a state reads as received."""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_sent_deliveries_read_as_received.py"
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("_mig_sent_state", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_sent_delivery_left_pending_reads_as_received_and_unsent_ones_stay(client):
    now = datetime.now(UTC)
    rows = {"sent": now, "unsent": None}
    ids = {key: uuid.uuid4() for key in rows}

    async def seed() -> None:
        async with client.test_factory() as s:
            for key, sent_at in rows.items():
                event = uuid.uuid4()
                await s.execute(
                    text(
                        "INSERT INTO deliveries (id, event_id, recipient_handle, "
                        "receiver_id, dedup_key, type, payload, event_at, recorded_at, "
                        "sent_at, attempts, state) VALUES (:id, :event, 'alice', 1, "
                        ":key, 'ROOM_NOTICE', '{}', :now, :now, :sent, 0, 'pending')"
                    ),
                    {
                        "id": ids[key],
                        "event": event,
                        "key": f"{event}:alice",
                        "now": now,
                        "sent": sent_at,
                    },
                )
            await s.commit()

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().upgrade()

    async def upgrade_and_read() -> dict:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()
        async with client.test_factory() as s:
            result = await s.execute(
                text("SELECT id, state FROM deliveries WHERE id = ANY(:ids)"),
                {"ids": list(ids.values())},
            )
            states = dict(result.all())
        return {key: states[ids[key]] for key in ids}

    asyncio.run(seed())
    assert asyncio.run(upgrade_and_read()) == {"sent": "received", "unsent": "pending"}
