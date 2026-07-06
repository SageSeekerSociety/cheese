"""Unit tests for the naive_api tool executor glue (fakes, no DB)."""

from typing import Any

import pytest

from app.agent.adapters.naive_api.executor import make_tool_executor
from app.agent.authorization.authorizer import ProjectActor

pytestmark = pytest.mark.anyio

_ACTOR = ProjectActor(kind="agent", actor_id=1, project_id=2)


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    async def commit(self) -> None:
        self.committed = True


class _Factory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


async def test_opens_session_invokes_and_commits():
    session = _FakeSession()
    seen: dict[str, Any] = {}

    class _Invoker:
        async def invoke(self, db, actor, name, args) -> dict:
            seen["call"] = (db, actor, name, args)
            return {"block_id": 5}

    execute = make_tool_executor(_Factory(session), lambda s: _Invoker())  # type: ignore[arg-type]
    result = await execute(_ACTOR, "post_message", {"x": 1})

    assert result == {"block_id": 5}
    db, actor, name, args = seen["call"]
    assert db is session and actor is _ACTOR and name == "post_message" and args == {"x": 1}
    assert session.committed
    assert session.closed


async def test_error_propagates_without_commit_but_closes():
    session = _FakeSession()

    class _FailInvoker:
        async def invoke(self, *a) -> Any:
            raise ValueError("boom")

    execute = make_tool_executor(_Factory(session), lambda s: _FailInvoker())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await execute(_ACTOR, "post_message", {})

    assert not session.committed  # no commit on failure
    assert session.closed  # context manager still exited
