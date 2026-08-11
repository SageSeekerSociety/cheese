"""Two rules about the shape of the tree, without a database.

本体 > 房间 > 事, and nothing deeper: work is the leaf. And when a piece of the
tree ends, everything still open underneath it ends with it — the same whether
it was archived by hand or ended the normal way, by being accepted.

The DB-backed versions of both rules live in
tests/integration/test_room_and_task.py; these cover the folding rules
themselves so they still run when the checks below them don't.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.review.services import AcceptService
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.services import TopicService


def _topic(kind: TopicKind, *, parent_id: uuid.UUID | None, title: str = "T") -> Topic:
    t = Topic()
    t.id = uuid.uuid4()
    t.kind = kind
    t.parent_id = parent_id
    t.title = title
    t.status = TopicStatus.active
    t.project_id = uuid.uuid4()
    return t


class _Children:
    """Stands in for TopicRepository.list_children."""

    def __init__(self, by_parent: dict[uuid.UUID, list[Topic]]):
        self._by_parent = by_parent

    async def list_children(self, topic_id: uuid.UUID) -> list[Topic]:
        return self._by_parent.get(topic_id, [])


class _EventLog:
    """Stands in for BlockRepository — records which topics got an event block."""

    def __init__(self) -> None:
        self.topic_ids: list[uuid.UUID] = []

    async def add(self, **kwargs) -> None:
        self.topic_ids.append(kwargs["topic_id"])


class _Session:
    """Stands in for AsyncSession, for the one query archiving makes: closing
    the topic's open accept cards (`review.archive.close_cards_for_archived_
    topic`). These topics carry no cards — answer with none."""

    async def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: [])


# --- 事不嵌套 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_room_holds_work_and_the_root_holds_rooms() -> None:
    svc = TopicService(SimpleNamespace())
    root = _topic(TopicKind.root, parent_id=None)
    room = _topic(TopicKind.topic, parent_id=root.id)

    assert await svc._placement(root) == (root.id, TopicKind.topic)
    assert await svc._placement(room) == (room.id, TopicKind.task)


@pytest.mark.anyio
async def test_a_child_of_a_task_becomes_its_sibling_in_the_room() -> None:
    """Work is the leaf: 拆 from inside a task puts the new task beside it.

    Before this, the child was simply created under the task, so 分身 nested
    under 分身 and the room stopped listing work that belonged to it.
    """
    svc = TopicService(SimpleNamespace())
    room = _topic(TopicKind.topic, parent_id=uuid.uuid4())
    task = _topic(TopicKind.task, parent_id=room.id)

    assert await svc._placement(task) == (room.id, TopicKind.task)


@pytest.mark.anyio
async def test_the_historical_subtopic_kind_is_a_leaf_too() -> None:
    """`subtopic` is what tasks were called before rooms existed."""
    svc = TopicService(SimpleNamespace())
    room = _topic(TopicKind.topic, parent_id=uuid.uuid4())
    legacy = _topic(TopicKind.subtopic, parent_id=room.id)

    assert await svc._placement(legacy) == (room.id, TopicKind.task)


@pytest.mark.anyio
async def test_a_parentless_task_sends_its_sibling_to_the_project_root() -> None:
    """Shouldn't exist; if it does, the tree still must not grow a second level."""
    svc = TopicService(SimpleNamespace())
    orphan = _topic(TopicKind.task, parent_id=None)
    root_id = uuid.uuid4()
    svc._projects = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(root_topic_id=root_id))
    )

    assert await svc._placement(orphan) == (root_id, TopicKind.task)


# --- 归档级联 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_the_cascade_takes_open_descendants_and_reports_them() -> None:
    svc = TopicService(_Session())
    room = _topic(TopicKind.topic, parent_id=None, title="运维")
    live = _topic(TopicKind.task, parent_id=room.id, title="还没干完")
    already = _topic(TopicKind.task, parent_id=room.id, title="早就归档")
    already.status = TopicStatus.archived
    svc._repo = _Children({room.id: [live, already]})
    svc._blocks = _EventLog()

    taken = await svc.archive_children(room, by="alice")

    assert [t.id for t in taken] == [live.id]
    assert live.status == TopicStatus.archived
    # An already-archived child is left alone — no second archive event on it.
    assert svc._blocks.topic_ids == [live.id]


@pytest.mark.anyio
async def test_accepting_archives_the_work_still_open_underneath(monkeypatch) -> None:
    """采纳即归档 cascades, exactly like manual 归档 already did.

    Accepting a room used to archive the room alone: its unfinished tasks stayed
    active under a delivered parent, each still holding a sandbox container.
    """
    import app.domain.topic.services as topic_services
    import app.domain.workspace.service as ws

    room = _topic(TopicKind.topic, parent_id=None, title="运维")
    live = _topic(TopicKind.task, parent_id=room.id, title="还没干完")
    log = _EventLog()
    monkeypatch.setattr(
        topic_services, "TopicRepository", lambda _s: _Children({room.id: [live]})
    )
    monkeypatch.setattr(topic_services, "BlockRepository", lambda _s: log)
    monkeypatch.setattr(
        topic_services, "ProjectRepository", lambda _s: SimpleNamespace()
    )
    monkeypatch.setattr(
        topic_services, "TopicMemberService", lambda _s: SimpleNamespace()
    )
    freed: list[uuid.UUID] = []
    monkeypatch.setattr(ws, "stop_topic_container", freed.append)

    now = datetime.now(UTC)
    await AcceptService(_Session())._archive_accepted(room, decided_by="alice", now=now)

    assert room.status == TopicStatus.archived
    assert room.accepted_by == "alice" and room.accepted_at == now
    assert live.status == TopicStatus.archived
    # Both containers go: the accepted topic's and the cascaded task's. Only the
    # parent's was being freed before.
    assert freed == [room.id, live.id]
