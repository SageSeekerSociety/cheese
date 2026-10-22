"""An AI teammate working on the platform itself reads the platform's run
records with its tool — the same as the admin page — and one in any other
project is refused: the records name other projects and their conversations."""

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    session_auth_headers,
)

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_tools():
    loader = SourceFileLoader("cheese_platform_tools_run_records", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load_tools()
DEV = "rr-dev"


def _room(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "查报错"},
        headers=session_auth_headers(DEV),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


class AgentHost:
    def __init__(self, client, project: str, topic: str):
        self.client = client
        self.environ = {"CHEESE_TOPIC": topic, "CHEESE_PROJECT": project}
        self.headers = room_agent_headers(client, topic)

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def run(self, **args) -> str:
        return cheese.run_platform_tool("cheese_run_records", args, self)


@pytest.fixture
def projects(client, monkeypatch) -> tuple[str, str]:
    """The platform's own project, and one that is not."""
    platform, other = (
        post_project(
            client, json={"name": name}, headers=session_auth_headers(DEV)
        ).json()["data"]["id"]
        for name in ("知是", "别的")
    )
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(platform).hex}/code"]
    )

    async def keep() -> None:
        async with client.test_factory() as s:
            s.add(
                RunRecord(
                    project_id=uuid.UUID(other),
                    kind="backend_error",
                    severity="error",
                    content="后端报错（GET /projects/x/site）：ConnectError",
                    meta={"fingerprint": "fp-site", "stack": "Traceback…ConnectError"},
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()

    client.portal.call(keep)
    return platform, other


def test_an_agent_on_the_platform_reads_the_errors_and_one_of_them_whole(
    client, projects
):
    platform, _ = projects
    agent = AgentHost(client, platform, _room(client, platform))

    listed = agent.run(group="errors")
    assert "ConnectError" in listed
    whole = agent.run(key="fp-site", kind="backend_error")
    assert "Traceback" in whole
    assert "别的" in whole


def test_the_agent_reads_one_row_per_turn_with_its_failure(client, projects):
    """`turns` reads the denominator the kind-per-line listing cannot: how many
    turns ran in the window, and which of them left a terminal event.

    Every state the formatter branches on is seeded here, and the header's two
    numbers are made to differ — otherwise "N 轮" passes on the page length
    alone and the denominator, the point of the tool, goes unpinned."""
    platform, _ = projects
    topic = _room(client, platform)
    agent = AgentHost(client, platform, topic)
    now = datetime.now(UTC)
    running, notice, failed, clean = (uuid.uuid4() for _ in range(4))

    async def seed() -> None:
        async with client.test_factory() as s:
            for turn_id, started, stopped in (
                (running, now - timedelta(minutes=1), None),
                (notice, now - timedelta(minutes=4), now - timedelta(minutes=3)),
                (failed, now - timedelta(minutes=7), now - timedelta(minutes=6)),
                (clean, now - timedelta(minutes=10), now - timedelta(minutes=9)),
            ):
                s.add(
                    AgentTurn(
                        id=turn_id,
                        conversation_id=uuid.UUID(topic),
                        continuation_id=turn_id,
                        author="alice",
                        started_at=started,
                        stopped_at=stopped,
                    )
                )
            s.add(
                Block(
                    project_id=uuid.UUID(platform),
                    conversation_id=uuid.UUID(topic),
                    author="platform",
                    author_type=AuthorType.platform,
                    kind=BlockKind.event,
                    content="",
                    turn_id=failed,
                    meta={"event_type": "turn_failed", "severity": "error"},
                )
            )
            # A notice borrowing the failure code: the room is waiting, nothing
            # is wrong, so the line must not call this turn failed.
            s.add(
                Block(
                    project_id=uuid.UUID(platform),
                    conversation_id=uuid.UUID(topic),
                    author="platform",
                    author_type=AuthorType.platform,
                    kind=BlockKind.event,
                    content="",
                    turn_id=notice,
                    meta={"event_type": "turn_failed", "severity": "info"},
                )
            )
            s.add(
                Block(
                    project_id=uuid.UUID(platform),
                    conversation_id=uuid.UUID(topic),
                    author="alice",
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="做点事",
                    meta={"prompted_turn": str(running), "prompt_attempts": 2},
                )
            )
            await s.commit()

    client.portal.call(seed)

    listed = agent.run(turns=True, limit=3)
    by_turn = {line.split("  ")[0]: line for line in listed.splitlines()}
    # Four turns in the window, three on the page: the two numbers are not the
    # same one written twice.
    assert "一共 4 次运行，这是最近的 3 次" in listed
    assert "失败 turn_failed" in by_turn[str(failed)]
    # Not failed, and not claimed to have succeeded either.
    assert "没记过失败" in by_turn[str(notice)]
    assert "还在跑" in by_turn[str(running)]
    assert "投喂 2 次" in by_turn[str(running)]
    # The open turn's last frame is said on the line: this backend reads no
    # session for it, so it reads 未知 — and never a fabricated 0 s. Dropping
    # the suffix from `_turns_lines` must take this assertion red.
    assert "最近帧 未知" in by_turn[str(running)]
    assert "0s 前" not in by_turn[str(running)]
    assert str(clean) not in listed
    # 成功不写事件：这一行只能说明平台没为这一轮记过失败。
    assert "不是记了成功" in listed


def test_the_agent_reads_one_turn_by_id_with_its_records(client, projects):
    """`turn_id` drills into the one turn the `turns` list showed: what it
    left in the run records, and how it ended. This backend reads no session
    for it, so its last frame says 未知 rather than 0."""
    platform, _ = projects
    topic = _room(client, platform)
    agent = AgentHost(client, platform, topic)
    now = datetime.now(UTC)
    turn_id = uuid.uuid4()

    async def seed() -> None:
        async with client.test_factory() as s:
            s.add(
                AgentTurn(
                    id=turn_id,
                    conversation_id=uuid.UUID(topic),
                    continuation_id=turn_id,
                    author="alice",
                    started_at=now - timedelta(minutes=5),
                )
            )
            s.add(
                RunRecord(
                    project_id=uuid.UUID(platform),
                    conversation_id=uuid.UUID(topic),
                    turn_id=turn_id,
                    kind="environment",
                    severity="info",
                    content="机器连上了",
                    created_at=now - timedelta(minutes=4),
                )
            )
            await s.commit()

    client.portal.call(seed)

    drilled = agent.run(turn_id=str(turn_id))
    assert str(turn_id) in drilled
    assert "没记过失败" in drilled
    assert "机器连上了" in drilled
    assert "最近帧 未知" in drilled
    assert "0s 前" not in drilled


def test_an_agent_elsewhere_is_refused(client, projects):
    _, other = projects
    agent = AgentHost(client, other, _room(client, other))

    with pytest.raises(cheese.PlatformToolError) as refusal:
        agent.run()
    assert "知是代码仓库" in str(refusal.value)
