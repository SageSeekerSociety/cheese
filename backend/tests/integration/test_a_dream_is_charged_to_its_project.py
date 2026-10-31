"""Consolidating a project's memory is work inside the project, so its team
pays for it like any other call there, and the usage records it as the
dream's: each of the dream's calls is made on the project's dream key, whose
spend is charged right after the dream as `memory_dream`. A room turn reads
only the rooms' key, so one ending while the dream runs cannot take the
dream's spend."""

import uuid

import pytest
from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory import dream
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project.repositories import ProjectRepository
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.models import ResourceUsage
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import project_service, registered
from tests.integration.test_gateway_usage import FakeGateway
from tests.support.dream_gateway import answer_dream_calls

# The fake answers only for `gw.utc_today()`, which the drain reads too, held
# still for each test (`pinned_clock`): a run across UTC midnight cannot split
# the two reads.
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("pinned_clock")]


class KeyedGateway(FakeGateway):
    """Serves each virtual key its own spend, so the test can tell whose spend
    landed where."""

    def __init__(self) -> None:
        super().__init__()
        self.by_key: dict[str, dict[str, tuple[int, int, float]]] = {}

    async def daily_spend_by_model(self, key, date):
        if date != gw.utc_today():
            return {}
        return {
            name: gw.ModelSpend(name, p, c, usd)
            for name, (p, c, usd) in self.by_key.get(key, {}).items()
        }


def _note(name: str) -> str:
    return f"---\nname: {name}\ndescription: 一条\ntype: feedback\n---\n\n一条\n"


async def _dreaming_project(factory, tmp_path, fake):
    channel = StubChannel()
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
        project = await project_service(session).create(name="P", owner_handle="alice")
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
    return svc, channel, pid, root


async def _rows(factory, pid) -> tuple[list[ResourceUsage], int]:
    """The project's charged usage rows: every row but the one seeded to make
    the dream due, which names no team."""
    query = select(ResourceUsage).where(
        ResourceUsage.project_id == pid, ResourceUsage.team_id.is_not(None)
    )
    async with factory() as session:
        rows = list(await session.scalars(query))
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    return rows, project.team_id


async def test_what_a_dream_spends_is_charged_to_the_project_as_the_dream(
    business_db_factory, tmp_path, monkeypatch
):
    answer_dream_calls(monkeypatch)
    fake = KeyedGateway()
    svc, channel, pid, root = await _dreaming_project(
        business_db_factory, tmp_path, fake
    )
    # Both keys minted as they are handed out: the rooms' to a room's call,
    # the dream key to the dream's own calls.
    room_key = await svc.project_gateway_key(pid)
    dream_key = await svc.project_gateway_key(pid, dream=True)
    assert room_key and dream_key and room_key != dream_key
    fake.by_key[dream_key] = {"deepseek-flash": (9000, 900, 0.40)}
    fake.by_key[room_key] = {"deepseek-flash": (100, 10, 0.04)}
    room_turn = uuid.uuid4()

    # A room turn that ends while the dream runs takes only the rooms' spend.
    await svc.charge_turn_spend(pid, root, room_turn)
    result = await svc.run_memory_dream(project_id=pid)
    await channel.runtime.stop_listening()
    assert result["status"] == "completed", result

    rows, team_id = await _rows(business_db_factory, pid)
    assert sorted(
        (r.kind, r.cost_usd, r.team_id, r.credits, r.turn_id == room_turn) for r in rows
    ) == [
        ("chat", pytest.approx(0.04), team_id, pytest.approx(0.04 / CREDIT_USD), True),
        (
            dream.DREAM_KIND,
            pytest.approx(0.40),
            team_id,
            pytest.approx(0.40 / CREDIT_USD),
            False,
        ),
    ]


async def test_a_dream_with_nothing_drained_says_it_ran_and_charges_nothing(
    business_db_factory, tmp_path, monkeypatch
):
    answer_dream_calls(monkeypatch)
    fake = KeyedGateway()
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
