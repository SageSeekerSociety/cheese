"""反馈认领资格（``claims.works_on_platform``）一次问完所有平台项目。

平台项目有几个，逐个 ``may_read_project`` 就要问几遍、每遍又是一串读库。这里把
「他在哪些项目里」换成按集合回答的假实现，数资格查询发了几次：六个平台项目、只有
最后一个是他的，只许问一次，结论和逐个问的版本一样。谁把循环改回逐个调用，计数就红。
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.feedback import claims

PROJECTS = [uuid.uuid4() for _ in range(6)]


def _per_project(readable: set[uuid.UUID]) -> bool:
    """The answer asking project by project gives."""
    return any(project_id in readable for project_id in PROJECTS)


@pytest.fixture
def asked(monkeypatch):
    """Fake the reads; returns (calls, readable) for the test to set and count."""
    calls: list[str] = []
    readable: set[uuid.UUID] = set()

    async def platform_projects(_session):
        return set(PROJECTS)

    class Session:
        """Hands back a project row for every platform project the select asks for."""

        async def scalars(self, _statement):
            return [SimpleNamespace(id=i) for i in PROJECTS]

    async def standings_in(_session, projects, _handle):
        calls.append("standings_in")
        return {p.id: (object() if p.id in readable else None) for p in projects}

    async def may_read_project(_session, *, project_id, handle):
        calls.append("may_read_project")
        return project_id in readable

    monkeypatch.setattr(claims, "platform_projects", platform_projects)
    monkeypatch.setattr(claims, "standings_in", standings_in, raising=False)
    monkeypatch.setattr(claims, "may_read_project", may_read_project, raising=False)
    return calls, readable, Session()


@pytest.mark.parametrize(
    "mine", [[PROJECTS[-1]], [], PROJECTS[:2]], ids=["last-only", "none", "first-two"]
)
async def test_asks_once_and_answers_like_project_by_project(asked, mine):
    calls, readable, session = asked
    readable.update(mine)
    got = await claims.works_on_platform(session, "alice", room_project_id=None)
    assert calls == ["standings_in"]
    assert got is _per_project(readable)
