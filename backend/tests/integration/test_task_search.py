"""Searching a space's tasks by keywords, through GET /tasks.

What a person searching expects: every word they typed is found in the task's
name or intro (a whole word, or part of one), a task named after the words
comes before one that only mentions them, deleted tasks and other spaces'
tasks never appear, and the page and total describe the same result set.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)


def _space(api_client: TestClient, headers: dict[str, str]) -> tuple[int, int]:
    data = create_approved_space(
        api_client,
        json={
            "name": f"Search Space ({unique_int(10000000, 99999999)})",
            "intro": "A space for search",
            "description": "Test description",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=headers,
    ).json()["data"]["space"]
    return data["id"], data["defaultCategoryId"]


def _task(
    api_client: TestClient,
    headers: dict[str, str],
    space: tuple[int, int],
    *,
    name: str,
    intro: str,
) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": intro,
            "description": '{"type":"doc","content":[]}',
            "space": space[0],
            "categoryId": space[1],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int((datetime.now(UTC) + timedelta(days=3)).timestamp() * 1000),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approve = api_client.patch(
        f"/tasks/{task_id}", headers=headers, json={"approved": "APPROVED"}
    )
    assert approve.status_code == 200, approve.text
    return task_id


def _search(
    api_client: TestClient,
    headers: dict[str, str],
    space_id: int,
    keywords: str,
    **params: object,
) -> dict:
    resp = api_client.get(
        "/tasks",
        params={"space": space_id, "keywords": keywords, **params},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _ids(data: dict) -> list[int]:
    return [t["id"] for t in data["tasks"]]


@pytest.fixture
def owner(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _headers(user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


class TestTaskKeywordSearch:
    def test_a_task_named_after_the_word_comes_before_one_that_mentions_it(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        # Created first and requested oldest-first, so any order but relevance
        # (creation time, id) puts it on top.
        mentions = _task(
            api_client,
            headers,
            space,
            name="前端组件重构",
            intro="顺便整理季度财务报表的导出格式",
        )
        named = _task(
            api_client, headers, space, name="季度财务报表整理", intro="按月汇总"
        )
        _task(api_client, headers, space, name="数据库迁移", intro="迁移到新集群")

        oldest_first = {"sort_by": "createdAt", "sort_order": "asc"}
        # The second request adds filters the search itself does not cover.
        for extra in ({}, {"limitedView": "true", "lifecycle": "notEnded"}):
            data = _search(
                api_client, headers, space[0], "财务报表", **oldest_first, **extra
            )
            assert _ids(data) == [named, mentions], extra

    def test_part_of_a_word_still_finds_the_task(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        target = _task(
            api_client, headers, space, name="季度财务报表整理", intro="按月汇总"
        )
        _task(api_client, headers, space, name="数据库迁移", intro="迁移到新集群")

        # 「务报」 straddles two words of 「财务报表」.
        assert _ids(_search(api_client, headers, space[0], "务报")) == [target]

    def test_english_and_mixed_queries(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        react = _task(
            api_client,
            headers,
            space,
            name="Build a React dashboard",
            intro="Use TypeScript hooks",
        )
        pg = _task(
            api_client, headers, space, name="数据库迁移PostgreSQL", intro="迁移旧库"
        )

        assert _ids(_search(api_client, headers, space[0], "react")) == [react]
        assert _ids(_search(api_client, headers, space[0], "Dashboard")) == [react]
        assert _ids(_search(api_client, headers, space[0], "postgresql 迁移")) == [pg]

    def test_every_word_must_be_found(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        both = _task(
            api_client, headers, space, name="季度财务报表整理", intro="导出为表格"
        )
        _task(api_client, headers, space, name="财务报表审计", intro="核对账目")

        assert _ids(_search(api_client, headers, space[0], "财务报表 导出")) == [both]
        assert _ids(_search(api_client, headers, space[0], "财务报表 天气")) == []

    def test_deleted_tasks_and_other_spaces_are_not_found(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        other_space = _space(api_client, headers)
        kept = _task(api_client, headers, space, name="年度预算编制", intro="汇总")
        deleted = _task(api_client, headers, space, name="年度预算复核", intro="汇总")
        _task(api_client, headers, other_space, name="年度预算编制", intro="汇总")

        assert (
            api_client.delete(f"/tasks/{deleted}", headers=headers).status_code == 204
        )

        data = _search(api_client, headers, space[0], "年度预算")
        assert _ids(data) == [kept]
        assert data["page"]["hasMore"] is False

    def test_pages_and_total_describe_the_same_results(
        self, api_client: TestClient, owner: CreatedUser
    ) -> None:
        headers = _headers(owner)
        space = _space(api_client, headers)
        matching = {
            _task(api_client, headers, space, name=f"客户回访记录 {i}", intro="电话")
            for i in range(3)
        }
        _task(api_client, headers, space, name="服务器扩容", intro="加两台")

        first = _search(api_client, headers, space[0], "回访", pageSize=2)
        assert len(first["tasks"]) == 2
        assert first["page"]["hasMore"] is True
        second = _search(
            api_client,
            headers,
            space[0],
            "回访",
            pageSize=2,
            pageStart=first["page"]["nextStart"],
        )
        assert second["page"]["hasMore"] is False
        assert set(_ids(first)) | set(_ids(second)) == matching
        assert len(_ids(first) + _ids(second)) == 3
