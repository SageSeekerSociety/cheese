"""Authenticated Ask HTTP operations inside the fresh recovery interpreter.

Only the database/session provider and compute channel are fixture bindings.
Actor resolution, authorization, versioning and delivery dispatch are not mocked.
The ASGI transport runs routes/middleware, not the complete application lifespan.
"""

import asyncio
import json
import sys
import time
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
    next_scan = time.monotonic() + 30
    observations = []

    async def retry_due():
        nonlocal next_scan
        if time.monotonic() < next_scan:
            return
        from app.domain.delivery.timer import deliver_due

        result = await deliver_due(factory, chat=chat, runner=runner)
        next_scan = time.monotonic() + 30
        observations.append({"pump": result})

    async def observe(deliveries, rows):
        status = await channel.call(channel.handle, "ping", {})
        state = {
            "deliveries": [
                {
                    "id": str(d.id),
                    "state": d.state,
                    "attempt": str(d.attempt_id),
                    "attempts": d.attempts,
                    "retry_at": str(d.retry_at),
                }
                for d in deliveries
            ],
            "unfinished": [str(r.input_id) for r in rows if not r.completed_at],
            "working": status["working"],
            "work_id": status.get("work_id"),
        }
        if not observations or observations[-1] != state:
            observations.append(state)

    async def settled(expected):
        async with asyncio.timeout(90):
            while True:
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
        # Under this ClaudeCode harness an input's acceptance *is* its native
        # echo: receipts.py stamps accepted_at only for the "accepted" evidence
        # a harness whose runner taking an input is its reading produces,
        # which ClaudeCode never does. So "accepted"
        # means echoed_at, and the echo must be drained to be observed.
        async with asyncio.timeout(90):
            while True:
                await retry_due()
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
                    await observe(deliveries, rows)
                    if (
                        len(deliveries) == expected
                        and all(
                            any(
                                r.delivery_id == d.id
                                and r.attempt_id == d.attempt_id
                                and r.echoed_at
                                for r in rows
                            )
                            if accepted_only
                            else d.state == "received"
                            for d in deliveries
                        )
                        and len(rows) == expected + 1
                        and all(
                            r.echoed_at
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
            path = f"/topics/blocks/{question}/answers"
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
                (1, 0) if descriptor["mode"] == "http-idle" else (0, 1)
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
                # Publish the correction target and release the busy turn's
                # gate *before* waiting for the correction to be echoed. The
                # model continuation that drives the original executor reads
                # this target to know which echo completes the correction, so
                # waiting for the echo first deadlocks against it (the
                # correction is never echoed while the target is absent).
                # Wait only for the correction input to be *registered* (its
                # identity fields), then publish and let received(...) below
                # verify the echo the continuation now produces.
                target = None
                async with asyncio.timeout(30):
                    while target is None:
                        async with factory() as session:
                            delivery = await session.scalar(
                                select(Delivery).where(
                                    Delivery.topic_id == topic,
                                    Delivery.payload["v"].as_integer() == 2,
                                )
                            )
                            inputs = (
                                []
                                if delivery is None
                                else list(
                                    await session.scalars(
                                        select(NativeInput).where(
                                            NativeInput.delivery_id == delivery.id,
                                            NativeInput.attempt_id
                                            == delivery.attempt_id,
                                        )
                                    )
                                )
                            )
                            if len(inputs) == 1:
                                target = inputs[0]
                                payload = {
                                    "delivery_id": str(delivery.id),
                                    "attempt_id": str(target.attempt_id),
                                    "input_id": str(target.input_id),
                                    "work_id": str(target.work_id),
                                    "native_session_id": target.native_session_id,
                                    "recipient_handle": target.recipient_handle,
                                }
                                Path(descriptor["correction_target"]).write_text(
                                    json.dumps(payload)
                                )
                        if target is None:
                            await asyncio.sleep(0.05)
                Path(descriptor["gate"]).touch()
                await received(2, accepted_only=True)
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
        print(
            "HTTP_RECOVERY_TRACE " + json.dumps(observations),
            file=sys.stderr,
            flush=True,
        )
        await runner.drain(10)
        app.dependency_overrides.clear()
