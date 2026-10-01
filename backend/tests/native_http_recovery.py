"""Authenticated Ask HTTP operations inside the fresh recovery interpreter.

Only the database/session provider and compute channel are fixture bindings.
Actor resolution, authorization, versioning and delivery dispatch are not mocked.
The ASGI transport runs routes/middleware, not the complete application lifespan.
"""

import asyncio
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.core.db import get_db
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import Delivery, NativeInput
from app.main import app


async def answer_after_recovery(descriptor, chat, channel, factory):
    topic = uuid.UUID(descriptor["topic"])
    question = uuid.UUID(descriptor["question"])
    runner = get_work_runner()

    async def database():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_chat_service] = lambda: chat
    runner.subscribe_messages()

    async def settled(expected):
        async with asyncio.timeout(90):
            while True:
                for subscription in channel.runtime.subscriptions.values():
                    await subscription.drain()
                async with factory() as session:
                    rows = list(
                        await session.scalars(
                            select(NativeInput).where(NativeInput.topic_id == topic)
                        )
                    )
                    if len(rows) == expected and all(row.completed_at for row in rows):
                        break
                await asyncio.sleep(0.05)
        await runner.drain(10)

    async def received(expected, *, accepted_only=False):
        # HTTP commits intent before the background runner performs the RPC.
        # Establish its durable receipt before measuring retry/correction effects.
        async with asyncio.timeout(90):
            while True:
                for subscription in channel.runtime.subscriptions.values():
                    await subscription.drain()
                async with factory() as session:
                    deliveries = list(
                        await session.scalars(
                            select(Delivery).where(Delivery.topic_id == topic)
                        )
                    )
                    rows = list(
                        await session.scalars(
                            select(NativeInput).where(NativeInput.topic_id == topic)
                        )
                    )
                    if (
                        len(deliveries) == expected
                        and all(
                            d.state in ("sending", "received")
                            if accepted_only
                            else d.state == "received"
                            for d in deliveries
                        )
                        and len(rows) == expected + 1
                        and all(
                            r.accepted_at
                            if accepted_only
                            else r.echoed_at and r.settled_at
                            for r in rows
                        )
                    ):
                        break
                await asyncio.sleep(0.05)
        await runner.drain(10)

    def body(option, op, version):
        # A supplied author must not override the authenticated answerer.
        return {
            "kind": "option",
            "option": option,
            "client_op_id": op,
            "expect_version": version,
            "author": "bob",
        }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            path = f"/topics/blocks/{question}/answer"
            first_body = body("继续", "fresh-http-first", 0)
            first = await http.post(
                path, json=first_body, headers=descriptor["alice_headers"]
            )
            assert first.status_code == 200, first.text
            assert first.json()["data"]["meta"]["answer_log"][-1]["by"] == "alice"
            await received(1)
            if descriptor["mode"] == "http-idle":
                await settled(2)
            assert (channel.calls.count("send"), channel.calls.count("steer")) == (
                (0, 1) if descriptor["mode"] == "http-busy" else (1, 0)
            )
            before = (channel.calls.count("send"), channel.calls.count("steer"))
            retry = await http.post(
                path, json=first_body, headers=descriptor["alice_headers"]
            )
            assert retry.status_code == 200, retry.text
            wrong = await http.post(
                path,
                json=body("更正", "other-person", 1),
                headers=descriptor["bob_headers"],
            )
            assert wrong.status_code == 422 and "原答者" in wrong.text, wrong.text
            assert before == (channel.calls.count("send"), channel.calls.count("steer"))
            corrected = await http.post(
                path,
                json=body("更正", "fresh-http-correction", 1),
                headers=descriptor["alice_headers"],
            )
            assert corrected.status_code == 200, corrected.text
            log = corrected.json()["data"]["meta"]["answer_log"]
            assert [(e["v"], e["option"], e["by"]) for e in log] == [
                (1, "继续", "alice"),
                (2, "更正", "alice"),
            ]
            if descriptor["mode"] == "http-busy":
                # Release model completion after RPC acceptance. The gated model
                # response prevents the queued correction's echo until released.
                await received(2, accepted_only=True)
                Path(descriptor["gate"]).touch()
            await received(2)
            await settled(3)

        async with factory() as session:
            original = await session.get(Block, question)
            assert original.author == descriptor["agent"]
            answers = list(
                await session.scalars(
                    select(Block).where(
                        Block.topic_id == topic,
                        Block.meta["answer_to"].as_string() == str(question),
                    )
                )
            )
            assert len(answers) == 2 and {b.author for b in answers} == {"alice"}
            deliveries = list(
                await session.scalars(
                    select(Delivery).where(
                        Delivery.topic_id == topic,
                    )
                )
            )
            assert len(deliveries) == 2
            assert all(d.state == "received" for d in deliveries)
            rows = list(
                await session.scalars(
                    select(NativeInput).where(
                        NativeInput.topic_id == topic,
                    )
                )
            )
            assert {r.recipient_handle for r in rows} == {descriptor["agent"]}
            assert descriptor["default_seat"] not in {r.recipient_handle for r in rows}
            for block in answers:
                owners = [r for r in rows if str(block.id) in r.held_block_ids]
                assert len(owners) == 1
                assert consumed_turn(block) == str(owners[0].execution_work_id)
        return {
            "versions": 2,
            "unauthorized_correction": wrong.status_code,
            "answerer": "alice",
            "recipient": descriptor["agent"],
        }
    finally:
        await runner.drain(10)
        app.dependency_overrides.clear()
