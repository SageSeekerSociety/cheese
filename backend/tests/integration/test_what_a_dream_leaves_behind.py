"""整理跑完留下了什么：读过谁的 private，就只能写回谁的 private；房间里什么都不留。

整理按作用域分开跑：team 一份，每个有 private 的人各一份。某个人 private 里的文件名
和内容、某个人私聊里说过的话，都不能出现在任何别人读得到的地方——不论整理的那几次
调用答回来什么，也不论项目的座位上坐着一个什么样的 agent。整理不在任何房间里说话：
改动在记忆面板上看。
"""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func, select

from app.api import deps as session_turn_deps
from app.core import alerting
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent_instance.adapters import named_teammate
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.memory.files import MEMORY_ROOT, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.models import MemoryDreamRun
from app.domain.run_record.models import RunRecord
from app.domain.topic.services import TopicService
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import (
    project_service,
    registered,
    session_auth_headers,
)
from tests.integration.test_gateway_usage import FakeGateway
from tests.support.dream_gateway import answer_dream_calls, prompt_of, system_of

SECRET = "only-bob-knows"
#: Said only in bob's private chat with the project's 芝士.
SECRET2 = "said-to-cheese-in-private"
#: What the private consolidation call writes back into bob's memory.
TIDIED = "整理过的个人偏好"
#: What the team consolidation call writes back into the team's memory.
TEAM_TIDIED = "改过的项目约定"


def _note(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body}\ntype: feedback\n---\n\n{body}\n"


def _tidies(asked: dict) -> dict:
    """A consolidation that rewrites the one memory of whichever tree it is
    given, and says what it was told."""
    told = f"{system_of(asked)}\n\n{prompt_of(asked)}"
    if SECRET in prompt_of(asked):
        return {
            "write": {f"{SECRET}.md": _note(SECRET, TIDIED)},
            "delete": [],
            "summary": f"改写了 {SECRET}：{told}",
        }
    return {
        "write": {"shared-rule.md": _note("shared-rule", TEAM_TIDIED)},
        "delete": [],
        "summary": f"改了项目约定：{told}",
    }


class WorstScreen(StubChannel):
    """The worst agent the overview's seat could hold: it repeats everything
    it was told, in its reply and as a message to the room its credential
    names."""

    builds_model_env = True

    def __init__(self, home: Path):
        super().__init__()
        self.home = home
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
            _note("shared-rule", told), encoding="utf-8"
        )
        StubChannel.emit_turn(self, topic_id, prompt, told, agent=agent)


def test_nothing_the_dream_read_privately_reaches_a_room(client, tmp_path, monkeypatch):
    """Whatever the consolidation is told and answers, and whatever an agent
    on the overview's seat does with what it is told — reply with it, post it
    to the room with the credential it was started with — no one but bob
    reads bob's private memory or his private chat, and bob's memory is still
    consolidated."""
    monkeypatch.setenv("HOME", str(tmp_path))
    answer_dream_calls(monkeypatch, _tidies)
    screen = WorstScreen(tmp_path)
    result, ids = client.portal.call(lambda: _dream(client, screen))
    assert result["status"] == "completed", result
    root, private_chat = ids["root"], ids["private_chat"]

    for topic_id, told in screen.told_in:
        posted = client.post(
            f"/topics/{topic_id}/messages",
            json={"request_id": str(uuid.uuid4()), "content": told},
            headers={"Authorization": f"Bearer {screen.credentials[topic_id]}"},
        )
        assert posted.status_code < 300, posted.text

    async def everything_shared() -> list[tuple[str, str]]:
        """Every record of the project anyone but bob may read: what was said
        and recorded in any room but his private chat, the team's memory, and
        the dream's own record."""
        async with client.test_request_factory() as session:
            blocks = (
                await session.scalars(
                    select(Block).where(Block.project_id == ids["project"])
                )
            ).all()
            records = (
                await session.scalars(
                    select(RunRecord).where(RunRecord.project_id == ids["project"])
                )
            ).all()
            team = await MemoryFileStore(session).list(
                ids["project"], MemoryFileScope.team, None
            )
            runs = (
                await session.scalars(
                    select(MemoryDreamRun).where(
                        MemoryDreamRun.project_id == ids["project"]
                    )
                )
            ).all()
        return [
            *(
                ("block", (row.content or "") + json.dumps(row.meta or {}))
                for row in blocks
                if row.conversation_id != private_chat
            ),
            *(
                ("record", (row.content or "") + json.dumps(row.meta or {}))
                for row in records
                if row.conversation_id != private_chat
            ),
            *(("team memory", row.content) for row in team),
            *(
                ("dream run", (run.summary or "") + json.dumps(run.files_changed))
                for run in runs
            ),
        ]

    leaked = [
        (where, text[:80])
        for where, text in client.portal.call(everything_shared)
        if SECRET in text or SECRET2 in text
    ]
    assert leaked == []

    transcript = client.get(
        f"/topics/{root}/transcript", headers=session_auth_headers("alice")
    )
    assert transcript.status_code == 200, transcript.text
    assert SECRET not in transcript.text
    assert SECRET2 not in transcript.text

    assert TIDIED in client.portal.call(lambda: _memory_of(client, ids, "bob"))


def test_a_dream_leaves_no_new_block_in_any_room(client, tmp_path, monkeypatch):
    """A dream that changes both the team's memory and a person's private
    memory says nothing in any room: not in the overview, not in that
    person's private chat, not in any room's 现场."""
    monkeypatch.setenv("HOME", str(tmp_path))
    answer_dream_calls(monkeypatch, _tidies)
    result, ids = client.portal.call(
        lambda: _dream(client, WorstScreen(tmp_path), count_before=True)
    )
    assert result["status"] == "completed", result
    assert client.portal.call(lambda: _said(client, ids)) == ids["said_before"]
    # The dream did change both trees, so silence is not just nothing done.
    assert TEAM_TIDIED in client.portal.call(lambda: _memory_of(client, ids, None))
    assert TIDIED in client.portal.call(lambda: _memory_of(client, ids, "bob"))


def test_the_run_record_counts_private_changes_and_names_none(
    client, tmp_path, monkeypatch
):
    """The dream's own record lists the team files it changed; what it did to
    each person's private memory is only counted there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    answer_dream_calls(monkeypatch, _tidies)
    result, ids = client.portal.call(lambda: _dream(client, WorstScreen(tmp_path)))
    assert result["status"] == "completed", result
    assert result["files"] == ["team/shared-rule.md"]
    summary = client.portal.call(lambda: _run_summary(client, ids))
    assert "private：1 人，改 1、删 0" in summary
    assert "bob" not in summary


def test_a_team_pass_that_deletes_too_much_is_refused_and_told_to_the_platform(
    client, tmp_path, monkeypatch
):
    """A team consolidation that would delete most of the team's memory
    writes nothing; the refusal goes to the platform's alerting, and no room
    hears of it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    alerted: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        alerting, "send", lambda title, lines, **_kw: alerted.append((title, lines))
    )

    def deletes_everything(asked: dict) -> dict:
        if SECRET in prompt_of(asked):
            return {"write": {}, "delete": [], "summary": ""}
        names = [f"rule-{i}.md" for i in range(6)]
        return {"write": {}, "delete": names, "summary": "删光"}

    answer_dream_calls(monkeypatch, deletes_everything)
    result, ids = client.portal.call(
        lambda: _dream(client, WorstScreen(tmp_path), extra_team=6, count_before=True)
    )
    assert result["status"] == "refused", result
    assert result["files"] == []
    team = client.portal.call(lambda: _memory_names(client, ids))
    assert {f"rule-{i}.md" for i in range(6)} <= team
    assert alerted and "team" in "\n".join(alerted[0][1])
    assert client.portal.call(lambda: _said(client, ids)) == ids["said_before"]


async def _memory_of(client, ids, owner: str | None) -> str:
    scope = MemoryFileScope.team if owner is None else MemoryFileScope.private
    async with client.test_request_factory() as session:
        rows = await MemoryFileStore(session).list(ids["project"], scope, owner)
    return "\n".join(row.content for row in rows)


async def _memory_names(client, ids) -> set[str]:
    async with client.test_request_factory() as session:
        rows = await MemoryFileStore(session).list(
            ids["project"], MemoryFileScope.team, None
        )
    return {row.path for row in rows}


async def _run_summary(client, ids) -> str:
    async with client.test_request_factory() as session:
        run = await session.scalar(
            select(MemoryDreamRun).where(MemoryDreamRun.project_id == ids["project"])
        )
    assert run is not None
    return run.summary or ""


async def _said(client, ids) -> tuple[int, int]:
    """How many blocks and run records the project's rooms hold."""
    async with client.test_request_factory() as session:
        blocks = await session.scalar(
            select(func.count(Block.id)).where(Block.project_id == ids["project"])
        )
        records = await session.scalar(
            select(func.count(RunRecord.id)).where(
                RunRecord.project_id == ids["project"]
            )
        )
    return int(blocks or 0), int(records or 0)


async def _dream(
    client,
    screen: StubChannel,
    *,
    extra_team: int = 0,
    count_before: bool = False,
) -> tuple[dict, dict[str, Any]]:
    """跑一次整理，交回它的结果，以及项目、总览和 bob 私聊的 id。"""
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
            *((MemoryFileScope.team, None, f"rule-{i}") for i in range(extra_team)),
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
        ids: dict[str, Any] = {
            "project": project.id,
            "root": project.root_topic_id,
            "private_chat": private_chat.id,
        }
        await session.commit()
    if count_before:
        ids["said_before"] = await _said(client, ids)

    result = await svc.run_memory_dream(project_id=ids["project"])
    await screen.runtime.stop_listening()
    return result, ids
