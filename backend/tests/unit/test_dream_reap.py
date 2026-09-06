"""记忆整理 hanging off the idle screen reaper: when a pass is worth a turn, and
why starting one can never stop a screen from being released.

The trap this file exists for: a pass writes blocks, and blocks are what "idle"
is measured on — so the naive version has every organized screen renew its own
lease off the very turn that was supposed to be its last, forever.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.memory.dream import latest_dream
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService
from tests.conftest import stub_compute

pytestmark = pytest.mark.anyio


class _Kickoffs:
    """Stand-in for the work runner: records the turns a sweep would start."""

    def __init__(self, boom: bool = False):
        self.started: list[tuple[uuid.UUID, str]] = []
        self._boom = boom

    def submit_kickoff(self, chat, topic_id, *, prompt=None):
        if self._boom:
            raise RuntimeError("模型网关挂了")
        self.started.append((topic_id, prompt or ""))
        return uuid.uuid4()


def _scheduler(client, tmp_path) -> SchedulerService:
    return SchedulerService(
        chat_service=ChatService(
            session_factory=client.test_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
            compute=stub_compute(),
        )
    )


async def _idle_topic(factory, *, blocks: int = 25):
    """A topic with a real history, last touched a month ago."""
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        repo = BlockRepository(session)
        for i in range(blocks):
            await repo.add(
                project_id=project.id,
                topic_id=topic.id,
                author="u",
                author_type=AuthorType.human,
                content=f"第 {i} 条",
            )
        await session.execute(
            update(Block)
            .where(Block.topic_id == topic.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()
        return project.id, topic.id


def _screen(project_id: uuid.UUID, topic_id: uuid.UUID):
    """The topic's screen on an online device — what the reaper walks."""
    from app.domain.agent.device_hub import HubScreen

    return HubScreen(
        sid=f"s-{topic_id.hex[:8]}",
        device_id="dev1",
        command=[],
        token=topic_id.hex[:8],
        agent_user_id=1,
        agent_handle="cheese",
        project_id=project_id,
        topic_id=topic_id,
    )


def _wire(monkeypatch, runner: _Kickoffs, screens: list, released: list):
    from app.api import deps
    from app.domain.agent import device_hub as dh
    from app.domain.agent import device_provider as dp

    monkeypatch.setattr(deps, "get_work_runner", lambda: runner)
    monkeypatch.setattr(dh.device_hub, "all_online_screens", lambda: list(screens))

    async def fake_release(project_id, topic_id, **kw):
        released.append(topic_id)

    monkeypatch.setattr(dp, "release_topic_screen", fake_release)


def _enable(monkeypatch, **overrides):
    monkeypatch.setattr(settings, "dream_enabled", True)
    monkeypatch.setattr(settings, "dream_min_blocks", 20)
    monkeypatch.setattr(settings, "dream_max_per_sweep", 1)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


async def test_dreaming_off_by_default_starts_nothing(client, tmp_path, monkeypatch):
    """Spending model budget on a background trigger is opt-in. Off, the reaper
    behaves exactly as it did before this feature existed."""
    project_id, topic_id = await _idle_topic(client.test_factory)
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    assert settings.dream_enabled is False

    freed = await _scheduler(client, tmp_path).reap_idle_device_screens(idle_hours=3)

    assert freed == 1
    assert runner.started == []
    assert released == [topic_id]
    async with client.test_factory() as session:
        assert await latest_dream(session, topic_id) is None


async def test_an_idle_screen_is_organized_before_it_is_released(
    client, tmp_path, monkeypatch
):
    """The pass runs INSIDE the screen, so this sweep starts it and leaves the
    screen alone; the next sweep does the releasing."""
    project_id, topic_id = await _idle_topic(client.test_factory)
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    _enable(monkeypatch)

    freed = await _scheduler(client, tmp_path).reap_idle_device_screens(idle_hours=3)

    assert freed == 0
    assert released == [], "the screen was closed out from under the pass"
    assert [t for t, _ in runner.started] == [topic_id]
    assert "记忆整理" in runner.started[0][1]
    async with client.test_factory() as session:
        recorded = await latest_dream(session, topic_id)
        assert recorded is not None and recorded.applied is False


async def test_the_screen_is_released_next_sweep_even_though_the_pass_spoke(
    client, tmp_path, monkeypatch
):
    """The loop trap. The pass posts blocks, so by the plain idle rule the topic
    is "active" and the screen outlives it — an hour later it looks idle again,
    gets organized again, and nothing is ever released."""
    project_id, topic_id = await _idle_topic(client.test_factory)
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    _enable(monkeypatch)
    svc = _scheduler(client, tmp_path)

    await svc.reap_idle_device_screens(idle_hours=3)
    async with client.test_factory() as session:
        recorded = await latest_dream(session, topic_id)
        assert recorded is not None
        turn_id = recorded.turn_id
        # What the pass leaves behind: its own blocks, timestamped just now.
        for content in ("正在整理记忆", "整理完了：合并 2 条，退休 1 条"):
            await BlockRepository(session).add(
                project_id=project_id,
                topic_id=topic_id,
                author="cheese",
                author_type=AuthorType.ai,
                content=content,
                turn_id=turn_id,
            )
        await session.commit()

    assert await svc.reap_idle_device_screens(idle_hours=3) == 1
    assert released == [topic_id]
    assert len(runner.started) == 1, "the topic was organized a second time"


async def test_someone_coming_back_still_keeps_the_screen(
    client, tmp_path, monkeypatch
):
    """The flip side: subtracting the pass's own blocks must not make the reaper
    blind to real activity, or a screen is closed under a live turn."""
    project_id, topic_id = await _idle_topic(client.test_factory)
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    _enable(monkeypatch)
    svc = _scheduler(client, tmp_path)

    await svc.reap_idle_device_screens(idle_hours=3)
    async with client.test_factory() as session:
        await BlockRepository(session).add(
            project_id=project_id,
            topic_id=topic_id,
            author="u",
            author_type=AuthorType.human,
            content="我回来了，继续之前的事",
        )
        await session.commit()

    assert await svc.reap_idle_device_screens(idle_hours=3) == 0
    assert released == []


async def test_a_failing_pass_never_holds_up_cleanup(client, tmp_path, monkeypatch):
    """记忆整理 is housekeeping. If it cannot start, the screen still goes."""
    project_id, topic_id = await _idle_topic(client.test_factory)
    runner, released = _Kickoffs(boom=True), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    _enable(monkeypatch)

    freed = await _scheduler(client, tmp_path).reap_idle_device_screens(idle_hours=3)

    assert freed == 1
    assert released == [topic_id]


async def test_one_sweep_organizes_at_most_the_configured_number(
    client, tmp_path, monkeypatch
):
    """A sweep that finds thirty idle screens must not start thirty turns."""
    topics = [await _idle_topic(client.test_factory) for _ in range(3)]
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(p, t) for p, t in topics], released)
    _enable(monkeypatch, dream_max_per_sweep=2)

    freed = await _scheduler(client, tmp_path).reap_idle_device_screens(idle_hours=3)

    assert len(runner.started) == 2
    # The third was not organized, and was not spared either — it is just reaped.
    assert freed == 1 and len(released) == 1


async def test_a_topic_too_thin_to_be_worth_a_turn_is_just_reaped(
    client, tmp_path, monkeypatch
):
    project_id, topic_id = await _idle_topic(client.test_factory, blocks=3)
    runner, released = _Kickoffs(), []
    _wire(monkeypatch, runner, [_screen(project_id, topic_id)], released)
    _enable(monkeypatch)

    freed = await _scheduler(client, tmp_path).reap_idle_device_screens(idle_hours=3)

    assert freed == 1
    assert runner.started == []
    assert released == [topic_id]
