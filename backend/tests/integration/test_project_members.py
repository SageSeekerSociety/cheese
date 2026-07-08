"""项目成员管理（连接器面）集成测试。

覆盖知是 2.0 工作区的扁平成员管理：任何成员都能列出 / 搜索候选 / 添加 / 移除成员，
且成员列表附带「是否为在线 agent」标志。

连接器路由通过传入的 ``session_factory`` 直接开会话（绕过 ``get_db``），因此这里把 router
挂到一个 mini app 上，并绑定到本测试的事务连接，让 seed 数据与路由共享同一事务。"""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub
from app.api.routes.connector_device import build_project_members_router
from app.common.auth import get_current_user_id
from app.core.errors import BaseError, base_error_handler
from app.domain.project.models import Project, ProjectMemberRole
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository
from app.domain.user.models import User, UserProfile

_next_seq = iter(range(1, 1_000_000))


def _seed_user(session: AsyncSession, *, username: str, nickname: str) -> User:
    now = datetime.now(UTC)
    user = User(username=username, email=f"{username}@t.local", created_at=now, updated_at=now)
    session.add(user)
    return user


class TestProjectMembersConnector:
    @pytest.fixture
    def env(
        self, db_connection: AsyncConnection, _portal: BlockingPortal
    ) -> Generator[tuple[TestClient, dict, dict[str, int]], None, None]:
        factory = async_sessionmaker(
            bind=db_connection,
            expire_on_commit=False,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        ids: dict[str, int] = {}

        async def _seed() -> None:
            async with factory() as session:
                owner = _seed_user(session, username="pm_owner", nickname="项目主")
                other = _seed_user(session, username="pm_other", nickname="待加入者")
                await session.flush()
                for u in (owner, other):
                    session.add(
                        UserProfile(
                            user_id=u.id,
                            nickname="项目主" if u is owner else "待加入者",
                            intro="",
                            avatar_id=1,
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                    )
                now = datetime.now(UTC)
                project = Project(
                    name="成员管理测试项目",
                    description="",
                    color_code="#5B8FF9",
                    content="",
                    leader_id=owner.id,
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(project)
                await session.flush()
                await ProjectMembershipRepository(session).add_member(
                    project_id=project.id, user_id=owner.id, role=ProjectMemberRole.OWNER
                )
                await session.commit()
                ids["owner"] = owner.id
                ids["other"] = other.id
                ids["project"] = project.id

        _portal.call(_seed)

        async def _is_member(project_id: int, user_id: int) -> bool:
            async with factory() as session:
                rel = await ProjectMembershipRepository(session).get_relation(project_id, user_id)
                if rel is not None:
                    return True
                proj = await ProjectRepository(session).get_by_id(project_id)
                return proj is not None and proj.leader_id == user_id

        app = FastAPI()
        app.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]
        app.include_router(build_project_members_router(DeviceHub(), factory, _is_member))

        acting: dict[str, int] = {"user_id": ids["owner"]}
        app.dependency_overrides[get_current_user_id] = lambda: acting["user_id"]

        client = TestClient(app, base_url="http://testserver")
        client.portal = _portal  # type: ignore[assignment]
        try:
            yield client, acting, ids
        finally:
            client.close()

    def test_list_members_marks_non_agent(
        self, env: tuple[TestClient, dict, dict[str, int]]
    ) -> None:
        client, _acting, ids = env
        resp = client.get(f"/connector/projects/{ids['project']}/members")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        m = body["members"][0]
        assert m["user_id"] == ids["owner"]
        assert m["is_agent"] is False  # 人类、无现场绑定
        assert m["role"] == ProjectMemberRole.OWNER.value

    def test_add_then_remove_member(
        self, env: tuple[TestClient, dict, dict[str, int]]
    ) -> None:
        client, acting, ids = env
        add = client.post(
            f"/connector/projects/{ids['project']}/members",
            json={"user_id": ids["other"]},
        )
        assert add.status_code == 200, add.text
        assert add.json()["member"]["user_id"] == ids["other"]

        listed = client.get(f"/connector/projects/{ids['project']}/members").json()
        assert {m["user_id"] for m in listed["members"]} == {ids["owner"], ids["other"]}

        # 扁平权限：新成员本身也能移除成员 —— 用 other 的身份删自己。
        acting["user_id"] = ids["other"]
        rm = client.delete(f"/connector/projects/{ids['project']}/members/{ids['other']}")
        assert rm.status_code == 200, rm.text

        acting["user_id"] = ids["owner"]
        after = client.get(f"/connector/projects/{ids['project']}/members").json()
        assert {m["user_id"] for m in after["members"]} == {ids["owner"]}

    def test_non_member_forbidden(
        self, env: tuple[TestClient, dict, dict[str, int]]
    ) -> None:
        client, acting, ids = env
        acting["user_id"] = ids["other"]  # 尚未加入
        resp = client.get(f"/connector/projects/{ids['project']}/members")
        assert resp.status_code == 403, resp.text

    def test_member_candidates_search(
        self, env: tuple[TestClient, dict, dict[str, int]]
    ) -> None:
        client, _acting, ids = env
        resp = client.get(
            f"/connector/projects/{ids['project']}/member-candidates",
            params={"q": "pm_other"},
        )
        assert resp.status_code == 200, resp.text
        cand_ids = {c["user_id"] for c in resp.json()["candidates"]}
        assert ids["other"] in cand_ids
        assert ids["owner"] not in cand_ids  # 已是成员，排除

        empty = client.get(f"/connector/projects/{ids['project']}/member-candidates")
        assert empty.json()["candidates"] == []
