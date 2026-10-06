"""整理跑完：记下它改了哪些，总览房间里只说 team 的那几条。

总览全项目都看得见，而一次整理读的是 team 加每个人的 private。某个人 private 里的
文件名、整理的人看着所有人的 private 写下的那段交代，都是那个人的内容，不能出现在
总览里。
"""

import uuid
from pathlib import Path

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.block.models import Block, BlockKind
from app.domain.memory.files import MEMORY_ROOT, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project.services import ProjectService
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import registered
from tests.support.run_records import records_of

SECRET = "only-bob-knows"
SUMMARY = f"整理完了，顺手看了 bob 的 {SECRET}。"


def _note(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body}\ntype: feedback\n---\n\n{body}\n"


class DreamingScreen(StubChannel):
    """整理那一轮：会话同时改了 team 的一条和 bob 的一条，最后交代一句。"""

    def __init__(self, home: Path):
        super().__init__()
        self.home = home

    def emit_turn(
        self, topic_id: uuid.UUID, prompt: str, reply: str, *, agent: str | None = None
    ) -> None:
        root = self.home / MEMORY_ROOT
        (root / "team" / "shared-rule.md").write_text(
            _note("shared-rule", "改过的项目约定"), encoding="utf-8"
        )
        (root / "private" / "bob" / f"{SECRET}.md").write_text(
            _note(SECRET, "改过的个人偏好"), encoding="utf-8"
        )
        super().emit_turn(topic_id, prompt, SUMMARY, agent=agent)


def test_the_overview_hears_about_team_files_and_nothing_private(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, said = client.portal.call(lambda: _dream_and_listen(client, tmp_path))
    assert any("team/shared-rule.md" in text for text in said), said
    assert not any(SECRET in text for text in said), said


def test_the_run_record_lists_every_file_the_dream_changed(
    client, tmp_path, monkeypatch
):
    """轮次结束的那次对账和整理收尾时的那次对账不交错：整理改过的都记得下来。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    result, _ = client.portal.call(lambda: _dream_and_listen(client, tmp_path))
    assert result["status"] == "completed", result
    assert sorted(result["files"]) == [
        f"private/bob/{SECRET}.md",
        "team/shared-rule.md",
    ]


async def _dream_and_listen(client, tmp_path) -> tuple[dict, list[str]]:
    """跑一次整理，交回它的结果和总览里每条事件、每条运行记录说的话（正文加展开的那一段）。"""
    factory = client.test_request_factory
    screen = DreamingScreen(tmp_path)
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "alice")
        await registered(session, "bob")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
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
        project_id, root_topic_id = project.id, project.root_topic_id
        await session.commit()

    result = await svc.run_memory_dream(project_id=project_id)

    async with factory() as session:
        events = (
            await session.scalars(
                select(Block).where(
                    Block.conversation_id == root_topic_id,
                    Block.kind == BlockKind.event,
                )
            )
        ).all()
        kept = await records_of(session, root_topic_id)
    return result, [
        (row.content or "") + str((row.meta or {}).get("detail") or "")
        for row in [*events, *kept]
    ]
