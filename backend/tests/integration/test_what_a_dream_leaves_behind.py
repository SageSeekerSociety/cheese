"""整理跑完留下了什么：读过谁的 private，就只能写回谁的 private。

整理分两份跑。team 那一轮是总览房间座位上的一轮会话，那个座位的会话、凭证、现场全
项目都看得见；每个人的 private 是平台单独发的一次模型调用。某个人 private 里的文件名
和内容、某个人私聊里说过的话，都不能出现在任何别人读得到的地方——不论那一轮会话里
的 agent 拿着它的凭证做了什么。
"""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.core.config import settings
from app.domain.agent import dream_usage
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent_instance.adapters import named_teammate
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.memory.files import MEMORY_ROOT, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.topic.services import TopicService
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import (
    project_service,
    registered,
    session_auth_headers,
)
from tests.integration.test_gateway_usage import FakeGateway
from tests.support.run_records import records_of

SECRET = "only-bob-knows"
#: Said only in bob's private chat with the project's 芝士.
SECRET2 = "said-to-cheese-in-private"
SUMMARY = f"整理完了，顺手看了 bob 的 {SECRET}。"
#: What the private consolidation call writes back into bob's memory.
TIDIED = "整理过的个人偏好"


def _note(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body}\ntype: feedback\n---\n\n{body}\n"


@pytest.fixture
def private_model(monkeypatch) -> list[dict]:
    """The gateway the private consolidation calls on, stubbed: it rewrites
    bob's one memory and says so. Returns what each call was asked."""
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        asked.append(json.loads(request.content))
        answer = {
            "write": {f"{SECRET}.md": _note(SECRET, TIDIED)},
            "delete": [],
            "summary": f"改写了 {SECRET}",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    # Nothing is drained from the stand-in gateway: do not wait for it.
    monkeypatch.setattr(dream_usage, "TURN_RETRY_AFTER", ())
    monkeypatch.setattr(dream_usage, "LATE_S", 0.0)
    return asked


class DreamingScreen(StubChannel):
    """整理那一轮：会话改了 team 的一条，最后交代一句。"""

    #: As production's machine channels do: the session's model environment is
    #: built by the channel, not handed over by the platform.
    builds_model_env = True

    def __init__(self, home: Path):
        super().__init__()
        self.home = home

    def emit_turn(
        self, topic_id: uuid.UUID, prompt: str, reply: str, *, agent: str | None = None
    ) -> None:
        root = self.home / MEMORY_ROOT
        (root / "team").mkdir(parents=True, exist_ok=True)
        (root / "team" / "shared-rule.md").write_text(
            _note("shared-rule", "改过的项目约定"), encoding="utf-8"
        )
        super().emit_turn(topic_id, prompt, SUMMARY, agent=agent)


class WorstScreen(DreamingScreen):
    """The worst agent the overview's seat could hold: it repeats everything
    it was told, in its reply and as a message to the room its credential
    names."""

    def __init__(self, home: Path):
        super().__init__(home)
        self.credentials: dict[uuid.UUID, str] = {}
        self.told_in: list[tuple[uuid.UUID, str]] = []

    @asynccontextmanager
    async def prepare_session(self, *, session, token, **kwargs):
        self.credentials[session.conversation_id] = token
        async with super().prepare_session(
            session=session, token=token, **kwargs
        ) as prepared:
            yield prepared

    def emit_turn(
        self, topic_id: uuid.UUID, prompt: str, reply: str, *, agent: str | None = None
    ) -> None:
        told = f"{self.last_system_prompt or ''}\n\n{prompt}"
        self.told_in.append((topic_id, told))
        root = self.home / MEMORY_ROOT
        (root / "team").mkdir(parents=True, exist_ok=True)
        (root / "team" / "shared-rule.md").write_text(
            _note("shared-rule", "改过的项目约定"), encoding="utf-8"
        )
        StubChannel.emit_turn(self, topic_id, prompt, told, agent=agent)


def test_the_overview_hears_about_team_files_and_nothing_private(
    client, tmp_path, monkeypatch, private_model
):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, said, _ = client.portal.call(
        lambda: _dream_and_listen(client, DreamingScreen(tmp_path))
    )
    assert any("team/shared-rule.md" in text for text in said), said
    assert not any(SECRET in text for text in said), said


def test_the_run_record_lists_every_file_the_dream_changed(
    client, tmp_path, monkeypatch, private_model
):
    """轮次结束的那次对账和整理收尾时的那次对账不交错：整理改过的都记得下来。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    result, _, _ = client.portal.call(
        lambda: _dream_and_listen(client, DreamingScreen(tmp_path))
    )
    assert result["status"] == "completed", result
    assert sorted(result["files"]) == [
        f"private/bob/{SECRET}.md",
        "team/shared-rule.md",
    ]


def test_nothing_the_dream_read_privately_reaches_a_room(
    client, tmp_path, monkeypatch, private_model
):
    """Whatever the dream's agent in the overview does with what it was told
    — reply with it, post it to the room with the credential it was started
    with — no one but bob reads bob's private memory or his private chat, and
    bob's memory is still consolidated."""
    monkeypatch.setenv("HOME", str(tmp_path))
    screen = WorstScreen(tmp_path)
    result, _, ids = client.portal.call(lambda: _dream_and_listen(client, screen))
    assert result["status"] == "completed", result
    root, private_chat = ids["root"], ids["private_chat"]

    # The agent posts everything it was told, as its tools would, with the
    # credential its session was started with.
    assert screen.told_in, "the dream's session was never told anything"
    for topic_id, told in screen.told_in:
        posted = client.post(
            f"/topics/{topic_id}/messages",
            json={"request_id": str(uuid.uuid4()), "content": told},
            headers={"Authorization": f"Bearer {screen.credentials[topic_id]}"},
        )
        assert posted.status_code < 300, posted.text

    async def everything_said() -> list[tuple[uuid.UUID, str]]:
        async with client.test_request_factory() as session:
            rows = (
                await session.scalars(
                    select(Block).where(Block.project_id == ids["project"])
                )
            ).all()
        return [
            (row.conversation_id, (row.content or "") + json.dumps(row.meta or {}))
            for row in rows
        ]

    leaked = [
        (conversation, text[:80])
        for conversation, text in client.portal.call(everything_said)
        if conversation != private_chat and (SECRET in text or SECRET2 in text)
    ]
    assert leaked == []

    transcript = client.get(
        f"/topics/{root}/transcript", headers=session_auth_headers("alice")
    )
    assert transcript.status_code == 200, transcript.text
    assert SECRET not in transcript.text
    assert SECRET2 not in transcript.text

    async def bobs_memory() -> str:
        async with client.test_request_factory() as session:
            rows = await MemoryFileStore(session).list(
                ids["project"], MemoryFileScope.private, "bob"
            )
        return next(row.content for row in rows if row.path == f"{SECRET}.md")

    assert TIDIED in client.portal.call(bobs_memory)


async def _dream_and_listen(
    client, screen: StubChannel
) -> tuple[dict, list[str], dict[str, uuid.UUID]]:
    """跑一次整理，交回它的结果、总览里每条事件和每条运行记录说的话（正文加展开的
    那一段），以及项目、总览和 bob 私聊的 id。"""
    factory = client.test_request_factory
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(screen.home / "ws"),  # type: ignore[attr-defined]
        gateway=cast(Any, FakeGateway()),  # duck-typed LlmGateway
    )
    async with factory() as session:
        await registered(session, "alice")
        await registered(session, "bob")
        project = await project_service(session).create(name="P", owner_handle="alice")
        project.settings = {
            **(project.settings or {}),
            "memory_dream": {"threshold_output_tokens": 1},
        }
        store = MemoryFileStore(session)
        for scope, owner, name in (
            (MemoryFileScope.team, None, "shared-rule"),
            (MemoryFileScope.private, "bob", SECRET),
        ):
            await store.write(
                project_id=project.id,
                scope=scope,
                owner_handle=owner,
                path=f"{name}.md",
                content=_note(name, "原来的"),
                updated_by="alice",
                expected_version=None,
            )
        private_chat = await TopicService(session).get_or_create_private(
            project_id=project.id, user_handle="bob", named_teammate=named_teammate
        )
        await BlockRepository(session).add(
            project_id=project.id,
            conversation_id=private_chat.id,
            author="bob",
            author_type=AuthorType.participant,
            content=f"只跟芝士说：{SECRET2}",
            kind=BlockKind.message,
        )
        await UsageRepository(session).add(
            project_id=project.id,
            conversation_id=None,
            model="m",
            input_tokens=0,
            output_tokens=10,
            cost_usd=0.0,
            kind="chat",
            turn_id=None,
        )
        assert project.root_topic_id is not None
        ids = {
            "project": project.id,
            "root": project.root_topic_id,
            "private_chat": private_chat.id,
        }
        await session.commit()

    result = await svc.run_memory_dream(project_id=ids["project"])
    await screen.runtime.stop_listening()

    async with factory() as session:
        events = (
            await session.scalars(
                select(Block).where(
                    Block.conversation_id == ids["root"],
                    Block.kind == BlockKind.event,
                )
            )
        ).all()
        kept = await records_of(session, ids["root"])
    return (
        result,
        [
            (row.content or "") + str((row.meta or {}).get("detail") or "")
            for row in [*events, *kept]
        ],
        ids,
    )
