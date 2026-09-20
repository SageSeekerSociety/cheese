"""The 壳 reaches the browser — over the API, not from a frontend copy.

The frontend renders whatever declaration it is handed and keeps no catalog of
its own (see `test_adding_a_shell_touches_no_component` in the unit suite), so
these tests are the only place the wire is pinned: if the resolution stops being
filled into `ProjectOut`, every project silently goes back to the default 壳 and
nothing errors.
"""

import uuid

from tests.conftest import seed_task_with_protocol
from tests.integration.conftest import session_auth_headers

OWNER = "shell-owner"


def _project_from(client, task_id: int) -> dict:
    return client.post(
        "/projects",
        json={"name": "壳测试", "owner_handle": OWNER, "external_task_id": task_id},
    ).json()["data"]


def _get(client, project_id: str) -> dict:
    r = client.get(f"/projects/{project_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_project_reports_the_shell_its_category_declared(client):
    task_id = seed_task_with_protocol(client, shell="course-student")
    project = _project_from(client, task_id)

    shell = _get(client, project["id"])["shell"]

    assert shell["name"] == "course-student"
    # The RESOLVED declaration, not just the name: the frontend must not own a
    # second copy of the catalog for a 壳 added server-side to reach the browser.
    assert shell["home"] == "overview"
    assert shell["terms"] == {"project": "课程", "topic": "提问"}
    assert "calendar" in shell["hidden"]
    assert shell["nav"]["tabs"][0] == "workspace"


def test_a_project_with_no_shell_anywhere_gets_the_default(client):
    """Every project that exists today, and every project created by hand."""
    project = client.post(
        "/projects", json={"name": "无边无际", "owner_handle": OWNER}
    ).json()["data"]

    shell = _get(client, project["id"])["shell"]

    assert shell["name"] == "default"
    assert shell["home"] == "workspace-running"
    assert shell["hidden"] == []
    assert shell["terms"] == {}


def test_a_task_override_replaces_the_categories_shell(client):
    task_id = seed_task_with_protocol(
        client, shell="course-student", override={"shell": "workbench"}
    )
    project = _project_from(client, task_id)

    shell = _get(client, project["id"])["shell"]

    assert shell["name"] == "workbench"
    assert shell["terms"] == {"project": "工作", "topic": "议题"}


def test_the_projects_own_setting_outranks_the_whole_protocol(client):
    task_id = seed_task_with_protocol(
        client, shell="course-student", override={"shell": "workbench"}
    )
    project = _project_from(client, task_id)
    pid = uuid.UUID(project["id"])

    import asyncio

    async def _set() -> None:
        from app.domain.project.repositories import ProjectRepository

        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = ProjectRepository(session)
            row = await repo.get(pid)
            assert row is not None
            await repo.set_settings(row, {**(row.settings or {}), "shell": "course-teacher"})
            await session.commit()

    asyncio.run(_set())

    shell = _get(client, project["id"])["shell"]

    assert shell["name"] == "course-teacher"
    assert shell["home"] == "workspace-running"


def test_the_list_route_carries_each_projects_own_shell(client):
    """The rail is built from the list, so a list without 壳 would make the app
    navigation snap back to the default the moment the page reloaded."""
    declared = _project_from(client, seed_task_with_protocol(client, shell="workbench"))
    plain = client.post(
        "/projects", json={"name": "普通的", "owner_handle": OWNER}
    ).json()["data"]

    # `/projects` without `team_id` means 「the CALLER's own projects」, and a
    # caller with no credential gets none — so this list is read as the owner.
    listing = client.get(
        "/projects", headers=session_auth_headers(OWNER)
    ).json()["data"]["data"]
    by_id = {p["id"]: p["shell"] for p in listing}

    assert by_id[declared["id"]]["name"] == "workbench"
    assert by_id[plain["id"]]["name"] == "default"
    assert by_id[plain["id"]]["home"] == "workspace-running"
