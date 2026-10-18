"""``/run-records`` 与 ``/throughput`` 的「平台自己项目里的房间」门禁只有一份实现。

两条只读路由都给做这个平台本身的 AI 队友用，门禁都是：
``place_or_404`` → ``resolver.resolve`` → ``authorize_topic(enforce=True)``
→ ``is_platform_project``（否则 403 ``say("runRecordsPlatformRoomsOnly")``）。
两处若各自保留一份 ``_from_platform_room``，任何一处加条件另一处都不会跟上。
"""

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import platform_room
from app.api.routes import run_records, throughput
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic

_ROUTES_DIR = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"


class _FakeDb:
    def __init__(
        self,
        *,
        rows: dict[tuple[type, uuid.UUID], object] | None = None,
        platform_project_ids: set[uuid.UUID] | None = None,
    ) -> None:
        self._rows = rows or {}
        self._platform_project_ids = platform_project_ids or set()

    async def get(self, model: type, key: uuid.UUID):
        return self._rows.get((model, key))

    async def scalars(self, _stmt):
        return list(self._platform_project_ids)


class _FakeResolver:
    def __init__(
        self,
        *,
        actor: object | None = None,
        auth_error: Exception | None = None,
    ) -> None:
        self.actor = actor or SimpleNamespace(handle="cheese")
        self.auth_error = auth_error
        self.resolved: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.authorized: list[tuple[object, uuid.UUID, uuid.UUID, bool]] = []

    async def resolve(self, *, topic_id: uuid.UUID, project_id: uuid.UUID):
        self.resolved.append((topic_id, project_id))
        return self.actor

    async def authorize_topic(
        self,
        actor: object,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        enforce: bool = False,
    ) -> None:
        self.authorized.append((actor, project_id, topic_id, enforce))
        if self.auth_error is not None:
            raise self.auth_error


def test_run_records_and_throughput_share_the_one_platform_room_gate() -> None:
    assert run_records.require_platform_room is platform_room.require_platform_room
    assert throughput.require_platform_room is platform_room.require_platform_room
    assert not hasattr(run_records, "_from_platform_room")
    assert not hasattr(throughput, "_from_platform_room")

    for route_file in ("run_records.py", "throughput.py"):
        tree = ast.parse((_ROUTES_DIR / route_file).read_text(encoding="utf-8"))
        fn_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        fn_names = [node.name for node in fn_nodes]
        assert "_from_platform_room" not in fn_names, (
            f"{route_file} still defines a local _from_platform_room"
        )
        assert "require_platform_room" not in fn_names, (
            f"{route_file} defines require_platform_room locally instead of importing"
        )
        handlers = [node for node in fn_nodes if node.decorator_list]
        assert handlers, f"{route_file} has no decorated route handlers"
        for handler in handlers:
            called = {
                call.func.id
                for call in ast.walk(handler)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            assert "require_platform_room" in called, (
                f"{route_file}:{handler.name} does not call require_platform_room"
            )


async def test_unknown_topic_raises_404_before_resolving_actor() -> None:
    missing_topic = uuid.uuid4()
    db = _FakeDb()
    resolver = _FakeResolver()

    with pytest.raises(NotFoundError) as exc_info:
        await platform_room.require_platform_room(db, resolver, missing_topic)

    assert exc_info.value.message == say("topicNotFound")
    assert resolver.resolved == []
    assert resolver.authorized == []


async def test_non_platform_project_room_raises_403_with_platform_rooms_only_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "docs_dev_repositories", ["org/platform-repo"])
    project_id = uuid.uuid4()
    room_id = uuid.uuid4()
    room = SimpleNamespace(id=room_id, project_id=project_id)
    db = _FakeDb(
        rows={(Topic, room_id): room},
        platform_project_ids={uuid.uuid4()},
    )
    resolver = _FakeResolver()

    with pytest.raises(ForbiddenError) as exc_info:
        await platform_room.require_platform_room(db, resolver, room_id)

    assert exc_info.value.message == say("runRecordsPlatformRoomsOnly")
    assert resolver.resolved == [(room_id, project_id)]
    assert resolver.authorized == [(resolver.actor, project_id, room_id, True)]


async def test_platform_project_room_and_task_pass_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "docs_dev_repositories", ["org/platform-repo"])
    project_id = uuid.uuid4()
    room_id = uuid.uuid4()
    task_id = uuid.uuid4()
    room = SimpleNamespace(id=room_id, project_id=project_id)
    task = SimpleNamespace(id=task_id, room_id=room_id)
    db = _FakeDb(
        rows={
            (Topic, room_id): room,
            (Task, task_id): task,
        },
        platform_project_ids={project_id},
    )
    resolver = _FakeResolver()

    assert await platform_room.require_platform_room(db, resolver, task_id) is None
    assert resolver.resolved == [(task_id, project_id)]
    assert resolver.authorized == [(resolver.actor, project_id, room_id, True)]
