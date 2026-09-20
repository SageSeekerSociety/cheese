"""RC transport behavior against Redis, including process-independent recovery."""

import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from redis.asyncio import Redis
from starlette.requests import Request

from app.api.routes.remote_control import bootstrap_session, launch_claims
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError, ConflictError, ForbiddenError
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.remote_control import RemoteControl, key, live_key


@pytest.fixture
def executor(tmp_path):
    workspace = tmp_path / "remote"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("REMOTE_CONTENT")
    runtime = (
        Path(__file__).parents[2]
        / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
    )
    target = {
        "command": [sys.executable, str(runtime)],
        "state": str(tmp_path / "state"),
    }
    subprocess.run(
        [*target["command"], "start", "--state", target["state"]],
        input=json.dumps(
            {"workspace": str(workspace), "claude": shutil.which("claude")}
        ),
        text=True,
        check=True,
        capture_output=True,
        timeout=15,
    )
    try:
        yield target, workspace
    finally:
        subprocess.run(
            [*target["command"], "stop", "--state", target["state"]],
            check=True,
            capture_output=True,
            timeout=15,
        )


async def test_remote_file_control_is_journalled_without_central_execution(
    rc, executor
):
    service, create = rc
    session = await create()
    target, workspace = executor
    payload = {
        "type": "control_request",
        "request_id": "remote-preview",
        "request": {"subtype": "read_file", "path": "sample.txt"},
    }
    await service.execute_remote(session, payload, "alice", target)
    result = await service.result(session["id"], "remote-preview")
    assert result["response"]["response"]["contents"] == "REMOTE_CONTENT"
    assert await service.redis.xlen(key(session["id"], "in")) == 0
    (workspace / "sample.txt").write_text("CHANGED_AFTER_RESPONSE")
    await RemoteControl(service.redis).execute_remote(session, payload, "alice", target)
    assert await service.result(session["id"], "remote-preview") == result


async def test_remote_file_control_records_unavailable_executor_error(rc, executor):
    service, create = rc
    session = await create()
    target, _ = executor
    target = target | {"state": target["state"] + "-unavailable"}
    payload = {
        "type": "control_request",
        "request_id": "remote-error",
        "request": {"subtype": "read_file", "path": "sample.txt"},
    }
    await service.execute_remote(session, payload, "alice", target)
    result = await service.result(session["id"], "remote-error")
    assert result["response"]["subtype"] == "error"
    assert await service.redis.xlen(key(session["id"], "in")) == 0


@pytest.fixture
async def rc(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "rc-test-signing-key-at-least-32-bytes")
    redis = Redis.from_url(settings.redis_url)
    service = RemoteControl(redis)
    sessions = []

    async def create():
        claims = {
            "p": str(uuid.uuid4()),
            "t": str(uuid.uuid4()),
            "exp": int(time.time()) + 7200,
        }
        session = await service.create(claims, {"title": "Fixture"})
        sessions.append(session)
        return session

    yield service, create
    for session in sessions:
        keys = [k async for k in redis.scan_iter(key(session["id"], "*"))]
        keys.append(key(session["topic_id"], "current"))
        await redis.delete(*keys)
    await redis.aclose()


async def test_worker_credential_cannot_cross_sessions_or_epochs(rc):
    service, create = rc
    first, second = await create(), await create()
    bridge = await service.bridge(first)
    assert (await service.authenticate_worker(first["id"], bridge["worker_jwt"]))[
        "id"
    ] == first["id"]
    with pytest.raises(AuthenticationRequiredError):
        await service.authenticate_worker(second["id"], bridge["worker_jwt"])
    renewed = await service.bridge(await service.get(first["id"]))
    assert renewed["worker_epoch"] > bridge["worker_epoch"]
    with pytest.raises(AuthenticationRequiredError):
        await service.authenticate_worker(first["id"], bridge["worker_jwt"])
    await service.update(first["id"], {"status": "archived"})
    with pytest.raises(AuthenticationRequiredError):
        await service.authenticate_worker(first["id"], renewed["worker_jwt"])


async def test_legacy_agent_index_moves_once_and_preserves_expiry(rc):
    service, create = rc
    session = await create()
    await service.update(session["id"], {"agent_handle": "agent-a"})
    old = live_key(session["topic_id"], None)
    new = live_key(session["topic_id"], "agent-a")
    await service.redis.expire(old, 90)
    try:
        found = await service.current(session["topic_id"], "agent-a")
        assert found["id"] == session["id"]
        assert not await service.redis.exists(old)
        assert 0 < await service.redis.ttl(new) <= 90
        assert (
            await RemoteControl(service.redis).current(session["topic_id"], "agent-a")
            == found
        )
    finally:
        await service.redis.delete(new)


@pytest.mark.parametrize("wrong_field", ["agent_handle", "topic_id"])
async def test_legacy_index_never_borrows_another_identity(rc, wrong_field):
    service, create = rc
    session = await create()
    changes = {"agent_handle": "agent-a", wrong_field: "another-identity"}
    await service.update(session["id"], changes)
    assert await service.current(session["topic_id"], "agent-a") is None
    assert await service.redis.get(live_key(session["topic_id"], None))
    assert not await service.redis.exists(live_key(session["topic_id"], "agent-a"))


async def test_existing_agent_index_wins_over_legacy_index(rc):
    service, create = rc
    legacy, current = await create(), await create()
    await service.update(legacy["id"], {"agent_handle": "agent-a"})
    new = live_key(legacy["topic_id"], "agent-a")
    await service.redis.set(new, current["id"], ex=90)
    try:
        assert (await service.current(legacy["topic_id"], "agent-a"))["id"] == current[
            "id"
        ]
        assert await service.redis.get(live_key(legacy["topic_id"], None))
    finally:
        await service.redis.delete(new)


async def test_concurrent_duplicate_control_enqueues_once(rc):
    service, create = rc
    session = await create()
    payload = {
        "type": "control_request",
        "request_id": "stop-once",
        "request": {"subtype": "interrupt"},
    }
    replies = await asyncio.gather(
        *(service.enqueue(session["id"], payload, "alice") for _ in range(10))
    )
    assert len({reply["event_id"] for reply in replies}) == 1
    assert await service.redis.xlen(key(session["id"], "in")) == 1
    with pytest.raises(ConflictError):
        await service.enqueue(
            session["id"],
            payload | {"request": {"subtype": "stop_task", "task_id": "other"}},
            "alice",
        )


async def test_reconnect_reads_queued_commands_but_never_reexecutes_offered_control(rc):
    service, create = rc
    session = await create()
    bridge = await service.bridge(session)
    session = await service.get(session["id"])
    await service.enqueue(
        session["id"],
        {
            "type": "control_request",
            "request_id": "one",
            "request": {"subtype": "interrupt"},
        },
        "alice",
    )
    stream = service.worker_stream(session, bridge["worker_jwt"], "0-0")
    assert await anext(stream) == ": connected\n\n"
    first = await anext(stream)
    assert '"request_id": "one"' in first
    await stream.aclose()
    # New service object represents a backend restart with the same Redis.
    recovered = RemoteControl(service.redis)
    await recovered.enqueue(
        session["id"],
        {
            "type": "control_request",
            "request_id": "two",
            "request": {"subtype": "initialize"},
        },
        "alice",
    )
    stream = recovered.worker_stream(session, bridge["worker_jwt"], "0-0")
    await anext(stream)
    next_event = await asyncio.wait_for(anext(stream), 2)
    assert '"request_id": "two"' in next_event
    assert '"request_id": "one"' not in next_event
    await stream.aclose()


async def test_pending_question_and_result_survive_restart_and_upload_retry(rc):
    service, create = rc
    session = await create()
    pending = {
        "type": "control_request",
        "uuid": "question-event",
        "request_id": "question",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "AskUserQuestion",
            "input": {"questions": []},
        },
    }
    await service.receive(session["id"], [{"payload": pending}], epoch=0)
    await service.receive(session["id"], [{"payload": pending}], epoch=0)
    recovered = RemoteControl(service.redis)
    snapshot = await recovered.snapshot(await recovered.get(session["id"]))
    assert snapshot["pending"]["question"] == pending
    assert len(await recovered.journal(session["id"], "0-0")) == 1
    response = {
        "type": "control_response",
        "uuid": "response-event",
        "response": {
            "subtype": "success",
            "request_id": "background",
            "response": {"backgrounded": False},
        },
    }
    await recovered.receive(session["id"], [{"payload": response}], epoch=0)
    assert await recovered.result(session["id"], "background") == response
    await recovered.redis.hdel(key(session["id"], "pending"), "question")
    await recovered.receive(session["id"], [{"payload": pending}], epoch=0)
    assert not (await recovered.snapshot(session))["pending"]


def request_with_token(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-cheese-token", token.encode())]})


async def test_bootstrap_requires_exact_place_scope_not_billing_header(rc, monkeypatch):
    service, create = rc
    session = await create()
    monkeypatch.setattr("app.api.routes.remote_control.store", lambda: service)
    token = mint_scoped_token(
        project_id=session["project_id"],
        topic_id=session["topic_id"],
        remote_control=True,
    )
    assert (await bootstrap_session(session["id"], request_with_token(token)))[
        "id"
    ] == session["id"]
    wrong = mint_scoped_token(
        project_id=session["project_id"],
        topic_id=str(uuid.uuid4()),
        remote_control=True,
    )
    with pytest.raises(ForbiddenError):
        await bootstrap_session(session["id"], request_with_token(wrong))
    ordinary = mint_scoped_token(
        project_id=session["project_id"], topic_id=session["topic_id"]
    )
    with pytest.raises(AuthenticationRequiredError):
        launch_claims(request_with_token(ordinary))


async def test_new_session_does_not_receive_old_sessions_output(rc):
    service, create = rc
    first = await create()
    second = await service.create(
        {"p": first["project_id"], "t": first["topic_id"], "exp": first["expires_at"]},
        {},
    )
    try:
        await service.receive(
            first["id"],
            [
                {
                    "payload": {
                        "type": "system",
                        "subtype": "init",
                        "uuid": "old",
                        "model": "old-model",
                    }
                }
            ],
            epoch=0,
        )
        current = await service.current(first["topic_id"])
        assert current["id"] == second["id"]
        assert not (await service.snapshot(current))["state"]
    finally:
        await service.redis.delete(key(second["id"]))


async def test_answer_replays_after_receipt_until_processed_even_past_sse_cursor(rc):
    service, create = rc
    session = await create()
    sid = session["id"]
    bridge = await service.bridge(session)
    session = await service.get(sid)
    epoch = bridge["worker_epoch"]
    pending = {
        "type": "control_request",
        "uuid": "question-event",
        "request_id": "question",
        "request": {"subtype": "can_use_tool", "input": {}},
    }
    await service.receive(sid, [{"payload": pending}], epoch=epoch)
    answer = {
        "type": "control_response",
        "uuid": "answer-question",
        "response": {
            "subtype": "success",
            "request_id": "question",
            "response": {"behavior": "allow", "updatedInput": {}},
        },
    }
    command = await service.enqueue(sid, answer, "alice")
    stream = service.worker_stream(session, bridge["worker_jwt"], "0-0")
    await anext(stream)
    first = await anext(stream)
    assert command["event_id"] in first
    await stream.aclose()
    await service.delivery(
        sid, [{"event_id": command["event_id"], "status": "received"}], epoch=epoch
    )
    assert "question" in (await service.snapshot(session))["pending"]
    assert (await service.command(sid, "answer-question"))["status"] == "received"
    recovered = RemoteControl(service.redis)
    stream = recovered.worker_stream(
        session, bridge["worker_jwt"], command["sequence_num"] + "-0"
    )
    await anext(stream)
    assert command["event_id"] in await anext(stream)
    await stream.aclose()
    await recovered.delivery(
        sid, [{"event_id": command["event_id"], "status": "processed"}], epoch=epoch
    )
    await recovered.delivery(
        sid, [{"event_id": command["event_id"], "status": "received"}], epoch=epoch
    )
    # An initialize snapshot taken before processing can arrive after its ACK.
    stale_snapshot = {
        "type": "control_response",
        "uuid": "initialize-result",
        "response": {
            "subtype": "success",
            "request_id": "cheese-initialize-1",
            "response": {"models": []},
            "pending_permission_requests": [pending],
        },
    }
    await recovered.receive(sid, [{"payload": stale_snapshot}], epoch=epoch)
    assert not (await recovered.snapshot(session))["pending"]
    assert (await recovered.snapshot(session))["state"]["initialize"] == {"models": []}
    assert (await recovered.enqueue(sid, answer, "alice"))["status"] == "processed"
    marker = {
        "type": "control_request",
        "request_id": "marker",
        "request": {"subtype": "initialize"},
    }
    await recovered.enqueue(sid, marker, "alice")
    stream = recovered.worker_stream(session, bridge["worker_jwt"], "0-0")
    await anext(stream)
    assert '"request_id": "marker"' in await asyncio.wait_for(anext(stream), 2)
    await stream.aclose()


async def test_offered_control_exposes_uncertainty_then_completion(rc):
    service, create = rc
    session = await create()
    sid = session["id"]
    bridge = await service.bridge(session)
    session = await service.get(sid)
    payload = {
        "type": "control_request",
        "request_id": "stop",
        "request": {"subtype": "interrupt"},
    }
    await service.enqueue(sid, payload, "alice")
    stream = service.worker_stream(session, bridge["worker_jwt"], "0-0")
    await anext(stream)
    await anext(stream)
    await stream.aclose()
    assert (await service.command(sid, "stop"))["status"] == "uncertain"
    assert (await service.enqueue(sid, payload, "alice"))["status"] == "uncertain"
    response = {
        "type": "control_response",
        "uuid": "stopped",
        "response": {"subtype": "success", "request_id": "stop"},
    }
    await service.receive(sid, [{"payload": response}], epoch=session["epoch"])
    assert (await service.command(sid, "stop"))["status"] == "completed"


async def test_stale_epoch_cannot_commit_events_or_acknowledge_answer(rc):
    service, create = rc
    session = await create()
    sid = session["id"]
    pending = {
        "type": "control_request",
        "uuid": "pending",
        "request_id": "question",
        "request": {},
    }
    await service.receive(sid, [{"payload": pending}], epoch=0)
    answer = {
        "type": "control_response",
        "uuid": "answer-question",
        "response": {"request_id": "question", "response": {}},
    }
    command = await service.enqueue(sid, answer, "alice")
    await service.bridge(session)
    late = {
        "type": "control_response",
        "uuid": "late-result",
        "response": {"request_id": "stop", "subtype": "success"},
    }
    with pytest.raises(AuthenticationRequiredError):
        await service.receive(sid, [{"payload": late}], epoch=0)
    with pytest.raises(AuthenticationRequiredError):
        await service.delivery(
            sid, [{"event_id": command["event_id"], "status": "processed"}], epoch=0
        )
    with pytest.raises(AuthenticationRequiredError):
        await service.update(
            sid, {"worker": {"external_metadata": {"stale": True}}}, epoch=0
        )
    assert len(await service.journal(sid, "0-0")) == 1
    assert await service.result(sid, "stop") is None
    assert "question" in (await service.snapshot(session))["pending"]
    assert "worker" not in await service.get(sid)


async def test_interrupted_upload_response_leaves_all_state_recoverable(
    rc, monkeypatch
):
    service, create = rc
    session = await create()
    sid = session["id"]
    pending = {
        "type": "control_request",
        "uuid": "question",
        "request_id": "q",
        "request": {"input": {"questions": []}},
    }
    response = {
        "type": "control_response",
        "uuid": "result",
        "response": {
            "request_id": "r",
            "subtype": "success",
            "response": {"items": []},
        },
    }
    events = [{"payload": pending}, {"payload": response}]
    original = service.redis.eval

    async def lost_reply(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("Backend lost its connection after Redis committed")

    with monkeypatch.context() as patch:
        patch.setattr(service.redis, "eval", lost_reply)
        with pytest.raises(ConnectionError):
            await service.receive(sid, events, epoch=0)
    recovered = RemoteControl(service.redis)
    assert (await recovered.snapshot(session))["pending"]["q"] == pending
    assert await recovered.result(sid, "r") == response
    await recovered.receive(sid, events, epoch=0)
    assert len(await recovered.journal(sid, "0-0")) == 2


async def test_invalid_batch_cannot_partially_commit(rc):
    service, create = rc
    session = await create()
    events = [
        {
            "payload": {
                "type": "control_request",
                "uuid": "valid",
                "request_id": "q",
                "request": {},
            }
        },
        {"payload": {"type": "control_request", "uuid": "invalid"}},
    ]
    with pytest.raises(ValueError):
        await service.receive(session["id"], events, epoch=0)
    assert not await service.journal(session["id"], "0-0")
    assert not (await service.snapshot(session))["pending"]


async def test_initialize_recovers_a_pending_question(rc):
    service, create = rc
    session = await create()
    pending = {
        "type": "control_request",
        "request_id": "question",
        "request": {"input": {"questions": []}},
    }
    response = {
        "type": "control_response",
        "uuid": "initialize-result",
        "response": {
            "subtype": "success",
            "request_id": "cheese-initialize-1",
            "response": {"models": []},
            "pending_permission_requests": [pending],
        },
    }
    await service.receive(session["id"], [{"payload": response}], epoch=0)
    snapshot = await RemoteControl(service.redis).snapshot(session)
    assert snapshot["pending"]["question"] == pending
    assert snapshot["state"]["initialize"] == {"models": []}


async def test_worker_stream_closes_cleanly_when_epoch_changes_while_reading(
    rc, monkeypatch
):
    service, create = rc
    session = await create()
    bridge = await service.bridge(session)
    session = await service.get(session["id"])
    await service.enqueue(
        session["id"],
        {
            "type": "control_request",
            "request_id": "stop",
            "request": {"subtype": "interrupt"},
        },
        "alice",
    )
    original = service.redis.xread

    async def renew_during_read(*args, **kwargs):
        rows = await original(*args, **kwargs)
        await service.bridge(session)
        return rows

    monkeypatch.setattr(service.redis, "xread", renew_during_read)
    stream = service.worker_stream(session, bridge["worker_jwt"], "0-0")
    await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert (await service.command(session["id"], "stop"))["status"] == "queued"
