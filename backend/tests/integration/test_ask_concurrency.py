"""Two answers that read the same version must not overwrite each other.

The barriers only choose arrival order. PostgreSQL owns both row locks, and an
independent connection observes B blocked by A before A is allowed to commit.
The route, authorization, writes and delivery producer are not replaced.
"""

import asyncio
import uuid
from contextvars import ContextVar

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sandbox_auth import SANDBOX_TOKEN
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.models import Delivery
from app.main import app
from tests.ask_fixtures import legacy_question
from tests.integration.conftest import post_project, session_auth_headers


@pytest.mark.parametrize(
    "case", ["same_operation", "different_operations", "corrections"]
)
def test_overlapping_answers_keep_one_winner(client, monkeypatch, case):
    auth = session_auth_headers("user-1")
    project = post_project(
        client, json={"name": "Overlapping answers"}, headers=auth
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T"},
        headers=auth,
    ).json()["data"]["id"]
    question = legacy_question(client, topic, question="Choose the next step")
    block_id = uuid.UUID(question["id"])
    initial = {
        "author": "user-1",
        "kind": "option",
        "option": "cursor",
        "expect_version": 0,
        "client_op_id": "initial",
    }
    if case == "corrections":
        response = client.post(
            f"/topics/blocks/{block_id}/answers", json=initial, headers=auth
        )
        assert response.status_code == 200, response.text

    a = {
        **initial,
        "client_op_id": "overlap-a",
        "expect_version": 1 if case == "corrections" else 0,
        "option": "pageStart" if case == "corrections" else "cursor",
    }
    b = (
        a.copy()
        if case == "same_operation"
        else {
            **a,
            "client_op_id": "overlap-b",
            "option": "cursor" if case == "corrections" else "pageStart",
        }
    )
    original_get = BlockRepository.get
    original_execute = AsyncSession.execute
    original_scalar = AsyncSession.scalar

    async def race():
        both_read = asyncio.Event()
        a_locked = asyncio.Event()
        b_submitted_lock = asyncio.Event()
        real_wait_observed = asyncio.Event()
        competitors = {}
        retained = []
        pids = {}
        requester = ContextVar("requester", default=None)

        async def initial_read(repo, identifier):
            block = await original_get(repo, identifier)
            name = requester.get()
            session = repo._session
            if identifier != block_id or name is None or session in competitors:
                return block
            assert name not in competitors.values()
            competitors[session] = name
            retained.append(block)
            pids[name] = (
                await original_execute(session, text("SELECT pg_backend_pid()"))
            ).scalar_one()
            if len(retained) == 2:
                both_read.set()
            await both_read.wait()
            if name == "B":
                await a_locked.wait()
            return block

        async def lock_query(session, statement, *args, **kwargs):
            name = competitors.get(session)
            is_answer_lock = (
                name is not None
                and getattr(statement, "_for_update_arg", None) is not None
                and getattr(statement, "is_select", False)
            )
            if not is_answer_lock:
                return await original_scalar(session, statement, *args, **kwargs)
            if name == "B":
                b_submitted_lock.set()
            result = await original_scalar(session, statement, *args, **kwargs)
            if name == "A":
                a_locked.set()
                await real_wait_observed.wait()
            return result

        monkeypatch.setattr(BlockRepository, "get", initial_read)
        monkeypatch.setattr(AsyncSession, "scalar", lock_query)

        async def observe_pg_wait():
            await b_submitted_lock.wait()
            async with client.test_request_factory() as observer:
                while True:
                    blockers = (
                        await original_execute(
                            observer,
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": pids["B"]},
                        )
                    ).scalar_one()
                    if pids["A"] in blockers:
                        real_wait_observed.set()
                        return
                    await asyncio.sleep(0.01)

        headers = {"X-Cheese-Token": SANDBOX_TOKEN, **session_auth_headers("user-1")}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
        ) as http:

            async def answer(name, payload):
                token = requester.set(name)
                try:
                    return await http.post(
                        f"/topics/blocks/{block_id}/answers", json=payload
                    )
                finally:
                    requester.reset(token)

            async with asyncio.timeout(15):
                results = await asyncio.gather(
                    answer("A", a),
                    answer("B", b),
                    observe_pg_wait(),
                )
        assert real_wait_observed.is_set(), "B never waited for A's PostgreSQL lock"
        assert len(competitors) == 2 and len(retained) == 2
        assert len(set(pids.values())) == 2

        # A fresh session reads what survived both commits, not either request's
        # identity map or its response serialization.
        async with client.test_request_factory() as observer:
            stored = await observer.get(Block, block_id)
            answers = list(
                await observer.scalars(
                    select(Block).where(
                        Block.meta["answer_to"].as_string() == str(block_id)
                    )
                )
            )
            deliveries = list(
                await observer.scalars(
                    select(Delivery).where(
                        Delivery.payload["answer_to"].as_string() == str(block_id)
                    )
                )
            )
            snapshot = {
                "statuses": [r.status_code for r in results[:2]],
                "log": stored.meta["answer_log"],
                "texts": [row.content for row in answers],
                "delivery_events": [str(row.event_id) for row in deliveries],
                "answer_events": [row.meta["delivery_event_id"] for row in answers],
            }
        expected_statuses = [200, 200] if case == "same_operation" else [200, 409]
        count = 2 if case == "corrections" else 1
        log = snapshot["log"]
        assert (
            snapshot["statuses"] == expected_statuses
            and len(log) == count
            and log[-1]["client_op_id"] == a["client_op_id"]
            and log[-1]["option"] == a["option"]
            and [entry["v"] for entry in log] == list(range(1, count + 1))
            and len(answers) == count
            and len(deliveries) == count
            and set(snapshot["answer_events"]) == set(snapshot["delivery_events"])
        ), snapshot
        if case == "corrections":
            assert log[0]["client_op_id"] == "initial", snapshot
            assert log[0]["option"] == "cursor", snapshot

    client.portal.call(race)
