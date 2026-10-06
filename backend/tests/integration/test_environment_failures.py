"""A channel's environment failed to prepare: told where people wait, read
back in the settings, gone back to on retry, and looked at on request.

The rules, as stated before the code was written:

- the conversation a teammate was working in when the environment failed is
  told once per failed attempt, with the end of the log;
- the project's environment settings show the channel's latest failure and
  how many conversations wait on it;
- retrying goes back to each of those conversations, once, and the failure
  is no longer open;
- 「让芝士看看」 answers about the latest failure only, and changes nothing.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from app.api.deps import get_chat_service
from app.domain.agent.environment_failures import tell_failed
from app.domain.block.models import Block
from app.domain.project import environment_diagnosis
from app.domain.project.environment_diagnosis import Diagnosis, parse
from app.domain.topic.models import Topic
from tests.integration.test_project_environment import project, room
from tests.support.threads import thread_in

FAILED = {
    "state": "failed",
    "attempt": "a1",
    "stage": "setup",
    "exit_code": 1,
    "log": "$ pnpm install\nERR_PNPM_UNSUPPORTED_ENGINE\nExpected >=22, got v20",
}


def _failed_in_a_thread(client, room_id: str, times: int = 1) -> uuid.UUID:
    chat = client.app.dependency_overrides[get_chat_service]()

    async def go():
        async with chat.session_factory() as db:
            channel = await db.get(Topic, uuid.UUID(room_id))
            thread = await thread_in(db, channel, "alice")
            for _ in range(times):
                await tell_failed(
                    db, room_id=channel.id, conversation_id=thread, status=FAILED
                )
            await db.commit()
            return thread

    return client.portal.call(go)


def _lines(client, conversation: uuid.UUID) -> list[Block]:
    chat = client.app.dependency_overrides[get_chat_service]()

    async def go():
        async with chat.session_factory() as db:
            from sqlalchemy import select

            return list(
                await db.scalars(
                    select(Block).where(
                        Block.conversation_id == conversation,
                        Block.meta["event_type"].as_string() == "environment_failed",
                    )
                )
            )

    return client.portal.call(go)


def test_a_failure_is_told_once_where_people_wait_and_shown_in_settings(client):
    project_id, headers = project(client)
    room_id = room(client, project_id)
    thread = _failed_in_a_thread(client, room_id, times=2)

    lines = _lines(client, thread)
    assert len(lines) == 1, "同一次失败说了不止一遍"
    assert "ERR_PNPM_UNSUPPORTED_ENGINE" in (lines[0].meta or {}).get("detail", "")

    rooms = client.get(f"/projects/{project_id}/environment", headers=headers).json()[
        "data"
    ]["rooms"]
    failure = next(r for r in rooms if r["id"] == room_id)["failure"]
    assert failure["waiting"] == 1
    assert "ERR_PNPM_UNSUPPORTED_ENGINE" in failure["log"]


def test_retrying_goes_back_to_where_people_waited(client, monkeypatch):
    project_id, headers = project(client)
    room_id = room(client, project_id)
    thread = _failed_in_a_thread(client, room_id)
    runner = SimpleNamespace(submit=Mock(return_value=uuid.uuid4()))
    monkeypatch.setattr("app.api.deps.get_work_runner", lambda: runner)

    base = f"/projects/{project_id}/environment"
    retried = client.post(
        f"{base}/rooms/{room_id}/apply", headers=headers, json={"latest": True}
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["resumed"] == 1
    assert [call.args[1] for call in runner.submit.call_args_list] == [thread]

    rooms = client.get(base, headers=headers).json()["data"]["rooms"]
    assert "failure" not in next(r for r in rooms if r["id"] == room_id)

    again = client.post(
        f"{base}/rooms/{room_id}/apply", headers=headers, json={"latest": True}
    )
    assert again.json()["data"]["resumed"] == 0, "重试第二次又回去了一遍"


def test_looking_at_a_failure_answers_about_it_and_changes_nothing(client, monkeypatch):
    project_id, headers = project(client)
    room_id = room(client, project_id)
    base = f"/projects/{project_id}/environment"

    nothing = client.post(f"{base}/rooms/{room_id}/diagnose", headers=headers)
    assert nothing.status_code >= 400, "没有失败也给出了诊断"

    _failed_in_a_thread(client, room_id)
    seen: dict = {}

    async def fake(session, *, config, failure, transport=None):
        seen["log"] = failure["log"]
        return Diagnosis(
            reason="Node 版本太旧",
            change="先切到 Node 22",
            setup_script="mise use node@22\npnpm install",
            startup_script=None,
            sure=True,
        )

    monkeypatch.setattr(environment_diagnosis, "diagnose", fake)
    before = client.get(base, headers=headers).json()["data"]["config"]
    answer = client.post(f"{base}/rooms/{room_id}/diagnose", headers=headers)
    assert answer.status_code == 200, answer.text
    data = answer.json()["data"]
    assert data["reason"] == "Node 版本太旧"
    assert data["setup_script"].startswith("mise use node@22")
    assert "ERR_PNPM_UNSUPPORTED_ENGINE" in seen["log"]
    assert client.get(base, headers=headers).json()["data"]["config"] == before


def test_an_answer_out_of_shape_is_no_answer():
    assert parse("我看不出来") is None
    assert parse('{"reason": ""}') is None
    told = parse(
        '前面的话 {"reason": "缺 Node 22", "setup_script": null, "sure": false}'
    )
    assert told is not None
    assert (told.reason, told.setup_script, told.sure) == ("缺 Node 22", None, False)
