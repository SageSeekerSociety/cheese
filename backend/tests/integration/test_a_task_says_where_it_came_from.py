"""A task's page says which message it came from, and where to find it.

The rules, stated before the code:

- a task turned from a message quotes that message, and names the
  conversation it is in, so the page can open it there: the main line, or the
  支线 when it was a reply in one;
- a task an AI teammate creates quotes the person's message that asked for it:
  in a 支线, the latest thing a person said there; in the main line, the latest
  message addressed to that teammate, never someone talking to someone else;
- a task nobody made from a message quotes nothing.

On dev (2026-10-11) people opened tasks and could not tell what they were for:
the page said where a task came from only at the very bottom, under the whole
document, and a task a teammate made in the main line said nothing at all.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.api.task_origin import ASKED_WITHIN, asked_in
from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from tests.integration.conftest import (
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics", json={"project_id": project["id"], "title": "修复"}, headers=ALICE
    ).json()["data"]["id"]
    return project["id"], channel, room_agent_seat(client, channel)


def _thread(client, block_id: str) -> str:
    opened = client.post(f"/blocks/{block_id}/thread", headers=ALICE)
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["id"]


def _source(client, task: str) -> dict | None:
    r = client.get(f"/topics/{task}/related", headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]["source"]


def _teammate_task(client, project: str, conversation: str, seat: str) -> str:
    made = client.post(
        f"/topics/{conversation}/teammate-tasks",
        json={"title": "修贴底", "summary": "回复变长时对话栏不再贴底"},
        headers={
            "Authorization": f"Bearer {
                mint_scoped_token(
                    project_id=project, topic_id=conversation, agent_handle=seat
                )
            }"
        },
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def test_a_message_turned_into_a_task_is_quoted_where_it_was_said(client):
    _project, channel, _seat = _channel(client)
    said = post_message(client, channel, "alice", {"content": "设置页的保存按钮没反应"})

    task = client.post(f"/blocks/{said['id']}/upgrade", headers=ALICE).json()["data"]

    source = _source(client, task["id"])
    assert source["block_id"] == said["id"]
    assert source["author"] == "alice"
    assert source["content"] == "设置页的保存按钮没反应"
    assert (source["where"], source["conversation_id"]) == ("channel", channel)
    assert source["title"] == "修复"


def test_a_reply_in_a_thread_turned_into_a_task_is_quoted_in_that_thread(client):
    _project, channel, _seat = _channel(client)
    root = post_message(client, channel, "alice", {"content": "对话栏有问题"})
    thread = _thread(client, root["id"])
    reply = post_message(client, thread, "alice", {"content": "回复变长时不贴底了"})

    task = client.post(f"/blocks/{reply['id']}/upgrade", headers=ALICE).json()["data"]

    source = _source(client, task["id"])
    assert source["block_id"] == reply["id"]
    assert source["content"] == "回复变长时不贴底了"
    assert (source["where"], source["conversation_id"]) == ("thread", thread)


def test_a_teammates_task_in_a_thread_quotes_what_the_person_said_there(client):
    project, channel, seat = _channel(client)
    root = post_message(client, channel, "alice", {"content": "对话栏有问题"})
    thread = _thread(client, root["id"])
    asked = post_message(
        client, thread, "alice", {"content": "经常停在中间，开个任务修一下"}
    )

    task = _teammate_task(client, project, thread, seat)

    source = _source(client, task)
    assert source["block_id"] == asked["id"]
    assert source["where"] == "thread"


def test_a_teammates_task_in_the_main_line_quotes_the_message_that_asked_it(client):
    project, channel, seat = _channel(client)
    asked = post_message(
        client, channel, "alice", {"content": f"<@{seat}> 把拖拽排序修好"}
    )
    post_message(client, channel, "alice", {"content": "今天谁值班？"})

    task = _teammate_task(client, project, channel, seat)

    source = _source(client, task)
    assert source["block_id"] == asked["id"]
    assert source["where"] == "channel"


def test_a_teammates_task_quotes_nothing_when_no_one_asked_it(client):
    project, channel, seat = _channel(client)
    post_message(client, channel, "alice", {"content": "今天谁值班？"})

    task = _teammate_task(client, project, channel, seat)

    assert _source(client, task) is None


def test_a_task_someone_created_on_its_own_quotes_nothing(client):
    _project, channel, _seat = _channel(client)
    task = client.post(
        f"/topics/{channel}/tasks", json={"title": "整理日志"}, headers=ALICE
    ).json()["data"]["id"]

    assert _source(client, task) is None


#: Reads of the two tables that answer "is this handle an agent" — ``user``
#: (the handle) and ``agent_bindings`` (which users carry one).
_IDENTITY_READ = re.compile(r'FROM\s+"?user"?\b|FROM\s+agent_bindings\b')


def _seed_asked_window(client, project: str, thread: str, seat: str) -> str:
    """A full window of messages handed to ``seat`` in ``thread``, oldest first.

    The newest ``ASKED_WITHIN - 1`` of them are the teammate's own; the oldest is
    the person's, so only a scan of the whole window can tell that apart — the
    answer sits at the far end of it. Returns the person's message id.
    """
    asked = uuid.uuid4()

    async def write() -> None:
        base = datetime.now(UTC) - timedelta(minutes=5)
        async with client.test_factory() as session:
            for i in range(ASKED_WITHIN):
                session.add(
                    Block(
                        id=asked if i == 0 else uuid.uuid4(),
                        project_id=uuid.UUID(project),
                        conversation_id=uuid.UUID(thread),
                        kind=BlockKind.message,
                        author_type=AuthorType.participant,
                        author="alice" if i == 0 else seat,
                        content=f"消息 {i}",
                        meta={"agent_recipient": {"handle": seat, "mentioned": True}},
                        created_at=base + timedelta(seconds=i),
                    )
                )
            await session.commit()

    asyncio.run(write())
    return str(asked)


def test_the_asking_message_is_found_with_one_identity_question_per_window(client):
    """A window handed to the teammate costs one identity lookup, not one per
    message.

    Each ``is_agent`` is two round trips, so the loop this file used to run
    asked twenty times to learn what one batched question answers. The answer
    must be unchanged — the person's message at the far end of the window, past
    nineteen of the teammate's own — and the identity tables must be read at
    most twice: the handles once, and which of them carry a binding once.
    """
    project, channel, seat = _channel(client)
    root = post_message(client, channel, "alice", {"content": "对话栏有问题"})
    thread = _thread(client, root["id"])
    asked = _seed_asked_window(client, project, thread, seat)

    async def read() -> str | None:
        async with client.test_request_factory() as session:
            block = await asked_in(session, uuid.UUID(thread), seat, main_line=False)
            return None if block is None else str(block.id)

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        found = client.portal.call(read)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert found == asked
    identity_reads = [s for s in statements if _IDENTITY_READ.search(s)]
    assert len(identity_reads) <= 2, (
        f"a window of {ASKED_WITHIN} messages cost {len(identity_reads)} "
        "identity reads; the per-message agent check is back.\n"
        + "\n".join(identity_reads)
    )
