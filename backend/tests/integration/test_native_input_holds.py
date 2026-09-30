"""Durable batch holds do not depend on process-local registration caches.

A child Python process queries PostgreSQL; it is not a restarted native executor.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace

from sqlalchemy import select

from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import record_receipt, register_input

_QUERY = """
import asyncio, json, sys, uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.domain.delivery.receipts import held_blocks
async def run():
    engine = create_async_engine(sys.argv[1])
    try:
        async with async_sessionmaker(engine)() as session:
            ids = await held_blocks(session, project_id=uuid.UUID(sys.argv[2]),
                topic_id=uuid.UUID(sys.argv[3]), recipient_handle=sys.argv[4])
            print(json.dumps(sorted(str(block) for block in ids)))
    finally:
        await engine.dispose()
asyncio.run(run())
"""


def test_new_process_finds_only_the_addressed_batch_and_echo_does_not_release_it(
    client,
):
    async def run():
        factory = client.test_request_factory
        identity = InputIdentity(
            uuid.uuid4(),
            uuid.uuid4(),
            "cheese-test",
            "claude_code",
            str(uuid.uuid4()),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        blocks = (uuid.uuid4(), uuid.uuid4())
        effects = InputEffects(held_block_ids=blocks)
        async with factory() as session:
            await register_input(session, identity, effects)
            await session.commit()
        for field, value in [
            ("project_id", uuid.uuid4()),
            ("topic_id", uuid.uuid4()),
            ("recipient_handle", "another-seat"),
        ]:
            other = replace(identity, **{field: value, "input_id": uuid.uuid4()})
            async with factory() as session:
                await register_input(
                    session, other, InputEffects(held_block_ids=(uuid.uuid4(),))
                )
                await session.commit()
        async with factory() as session:
            url = session.bind.url.render_as_string(hide_password=False)

        async def read_from_new_process():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _QUERY,
                url,
                str(identity.project_id),
                str(identity.topic_id),
                identity.recipient_handle,
                env={**os.environ, "PYTHONPATH": "."},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            return set(json.loads(stdout))

        assert await read_from_new_process() == {str(block) for block in blocks}
        async with factory() as session:
            await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.commit()
        assert await read_from_new_process() == {str(block) for block in blocks}
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert row.settled_at is not None
            assert row.block_ids == [], "A hold must not imply model consumption"

    client.portal.call(run)
