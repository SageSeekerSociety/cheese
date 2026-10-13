"""Consolidating a project's memory is work inside the project, so its team
pays for it like any other call there, and the usage records it as the
dream's: what the dream spent on the project's key is charged right after the
dream, as `memory_dream`, and is not left for the next room turn to charge
again as its own.

The dream runs the way production runs it: on a channel that builds its own
model environment (`builds_model_env`, as the central and owner channels do),
so the turn's calls go through the project's key."""

import uuid

import pytest
from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.domain.agent import dream_usage
from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory import dream
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.models import ResourceUsage
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import registered
from tests.integration.test_gateway_usage import FakeGateway

pytestmark = pytest.mark.anyio


class RemoteChannel(StubChannel):
    """A machine channel as production has them: it builds the session's
    model environment itself, so `_model_kwargs` hands it no key."""

    builds_model_env = True


def _note(name: str) -> str:
    return f"---\nname: {name}\ndescription: 一条\ntype: feedback\n---\n\n一条\n"


async def _dreaming_project(factory, tmp_path, fake):
    channel = RemoteChannel()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([channel.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        gateway=fake,  # duck-typed LlmGateway
    )
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        project.settings = {
            **(project.settings or {}),
            "memory_dream": {"threshold_output_tokens": 1},
        }
        await MemoryFileStore(session).write(
            project_id=project.id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path="rule.md",
            content=_note("rule"),
            updated_by="alice",
            expected_version=None,
        )
        # Enough written since the last dream for one to run.
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
        pid, root = project.id, project.root_topic_id
        await session.commit()
    # The project's key, minted when its machine's first call was admitted.
    assert await svc.project_gateway_key(pid)
    return svc, channel, pid, root


async def _rows(factory, pid) -> tuple[list[ResourceUsage], int]:
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(ResourceUsage).where(
                    ResourceUsage.project_id == pid, ResourceUsage.kind != "chat"
                )
            )
        )
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    return rows, project.team_id


async def test_what_a_dream_spends_is_charged_to_the_project_as_the_dream(
    business_db_factory, tmp_path
):
    fake = FakeGateway()
    svc, channel, pid, root = await _dreaming_project(
        business_db_factory, tmp_path, fake
    )
    fake.days[gw.utc_today()] = {"deepseek-flash": (9000, 900, 0.40)}

    result = await svc.run_memory_dream(project_id=pid)
    await channel.runtime.stop_listening()
    assert result["status"] == "completed", result
    # A room turn ending afterwards finds nothing new on the key.
    assert await svc.charge_turn_spend(pid, root, uuid.uuid4()) is None

    rows, team_id = await _rows(business_db_factory, pid)
    assert [(r.kind, r.cost_usd, r.team_id, r.credits) for r in rows] == [
        (
            dream.DREAM_KIND,
            pytest.approx(0.40),
            team_id,
            pytest.approx(0.40 / CREDIT_USD),
        )
    ]


async def test_a_dream_with_nothing_drained_says_it_ran_and_charges_nothing(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(dream_usage, "TURN_RETRY_AFTER", ())
    monkeypatch.setattr(dream_usage, "LATE_S", 0.0)
    fake = FakeGateway()
    svc, channel, pid, _root = await _dreaming_project(
        business_db_factory, tmp_path, fake
    )

    result = await svc.run_memory_dream(project_id=pid)
    await channel.runtime.stop_listening()
    assert result["status"] == "completed", result

    rows, team_id = await _rows(business_db_factory, pid)
    assert [(r.kind, r.team_id, r.credits) for r in rows] == [
        (f"{dream.DREAM_KIND}:unmetered", team_id, 0.0)
    ]
