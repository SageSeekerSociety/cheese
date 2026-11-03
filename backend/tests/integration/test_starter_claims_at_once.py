"""Many people claim a 题目 with starter code at the same moment (#4767 §二).

A claim records which archive it took and returns; the archive is unpacked and
pushed afterwards. So no claim waits on another's push — the task's claim lock
is held for the claim alone — and every repository still gets the starter code
once the push has run.

Real request transactions here (`app.core.db.get_db`, its lock timeout
included), not the shared per-test session: what is under test is two
transactions meeting on the same 题目.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import os
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from app.core import db as core_db
from app.domain.task import starter_jobs
from tests.conftest import seed_user
from tests.integration.conftest import create_approved_space
from tests.integration.test_task_files import (
    _auth,
    upload_root,  # noqa: F401
)
from tests.integration.test_task_starter_code import (
    _create_task,
    _main,
    _set_starter,
)

pytestmark = pytest.mark.usefixtures("upload_root")

#: Incompressible, so the archive is as large as what it unpacks to.
DATASET = 24 * 1024 * 1024


def _board(client) -> dict:
    token = seed_user(client, f"teacher{uuid.uuid4().hex[:8]}")
    response = create_approved_space(
        client,
        json={
            "name": f"Lab {uuid.uuid4().hex[:8]}",
            "intro": "lab",
            "description": "A lab board",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    space = response.json()["data"]["space"]
    return {
        "creator_token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("lab/README.md", "# Lab\n")
        archive.writestr("lab/dataset.bin", os.urandom(DATASET))
    return buffer.getvalue()


def test_two_claims_at_once_both_return_and_both_repositories_get_the_code(
    client, monkeypatch
):
    from app.common.auth import verify_access_token
    from app.main import app

    board = _board(client)
    task_id = _create_task(client, board, name="一起领的题")
    response = _set_starter(client, task_id, board["creator_token"], _archive())
    assert response.status_code == 200, response.text
    students = [seed_user(client, f"student{uuid.uuid4().hex[:8]}") for _ in range(2)]
    for token in students:
        response = client.post(
            f"/spaces/{board['space_id']}/members",
            json={"userId": verify_access_token(token).user_id},
            headers=_auth(board["creator_token"]),
        )
        assert response.status_code == 201, response.text

    monkeypatch.setattr(core_db, "async_session_factory", client.test_request_factory)
    monkeypatch.setitem(app.dependency_overrides, core_db.get_db, core_db.get_db)

    def claim(token: str) -> tuple[int, float, str]:
        started = time.monotonic()
        response = client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
        return response.status_code, time.monotonic() - started, response.text

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, students))

    for status, seconds, body in results:
        assert status == 200, body
        # Well inside the 5 s lock timeout a claim waiting on a push would hit.
        assert seconds < 5, seconds
    projects = [
        uuid.UUID(json.loads(body)["data"]["project"]["id"]) for _, _, body in results
    ]

    assert client.portal.call(starter_jobs.push_due, client.test_factory)["done"] == 2
    for project_id in projects:
        assert set(_main(project_id)) == {"README.md", "dataset.bin"}


def test_a_slow_repository_service_does_not_make_a_second_claim_fail(
    client, monkeypatch
):
    """The repository service takes 6 s to make each repository — longer than
    the 5 s a claim waits on a lock. Two people claiming at once both succeed:
    no claim waits on the service while holding something the other needs."""
    import asyncio

    from app.common.auth import verify_access_token
    from app.domain.project import forge
    from app.main import app

    board = _board(client)
    task_id = _create_task(client, board, name="仓库服务很慢时一起领的题")
    students = [seed_user(client, f"student{uuid.uuid4().hex[:8]}") for _ in range(2)]
    for token in students:
        response = client.post(
            f"/spaces/{board['space_id']}/members",
            json={"userId": verify_access_token(token).user_id},
            headers=_auth(board["creator_token"]),
        )
        assert response.status_code == 201, response.text

    # Whichever step talks to the service in this build, it answers slowly.
    for name in ("prepare_repository", "provision_repository"):
        quick = getattr(forge, name, None)
        if quick is None:
            continue

        async def slow(*args, _quick=quick, **kwargs):
            await asyncio.sleep(6)
            return await _quick(*args, **kwargs)

        monkeypatch.setattr(forge, name, slow)
    monkeypatch.setattr(core_db, "async_session_factory", client.test_request_factory)
    monkeypatch.setitem(app.dependency_overrides, core_db.get_db, core_db.get_db)

    def claim(token: str) -> tuple[int, str]:
        response = client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
        return response.status_code, response.text

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, students))

    for status, body in results:
        assert status == 200, body


def test_one_team_claiming_twice_at_once_makes_one_repository(client, monkeypatch):
    """Two claims by the same team at the same moment (two teammates submitting
    together) leave one project and one repository on the forge — not a second
    repository nobody uses, waiting for a sweep. The second is told at once to
    ask again, without waiting on the forge behind the first."""
    import asyncio

    from app.common.auth import verify_access_token
    from app.core.config import settings
    from app.domain.agent_instance.adapters import default_teammate
    from app.domain.living_doc.overview import seed_overview
    from app.domain.project import forge
    from app.domain.project.ports import Claim
    from app.domain.project.services import ProjectService
    from app.domain.task.adapters import TaskTerms, as_brief
    from app.domain.task.models import Task
    from app.domain.team.services import team_service
    from app.domain.usage.adapters import ComputeCredits

    board = _board(client)
    task_id = _create_task(client, board, name="一个团队同时领两次的题")
    handle = f"student{uuid.uuid4().hex[:8]}"
    student = seed_user(client, handle)
    user_id = verify_access_token(student).user_id

    # The forge answers slowly enough for the two claims to overlap there.
    for name in ("prepare_repository", "provision_repository"):
        quick = getattr(forge, name, None)
        if quick is None:
            continue

        async def slow(*args, _quick=quick, **kwargs):
            await asyncio.sleep(1)
            return await _quick(*args, **kwargs)

        monkeypatch.setattr(forge, name, slow)

    async def claim() -> uuid.UUID:
        async with client.test_request_factory() as session:
            task = await session.get(Task, task_id)
            service = ProjectService(
                session,
                default_teammate=default_teammate,
                compute_credits=ComputeCredits(session),
                overview=seed_overview,
                task_terms=TaskTerms(session),
            )
            project = await service.for_participation(
                task=as_brief(task),
                claim=Claim(member_id=user_id, is_team=False, approved=False),
                owner_handle=handle,
            )
            await session.commit()
            return project.id

    async def personal_team() -> None:
        # The student's own team exists already, as it does for anyone who has
        # used the platform; making it is not what the two claims race on.
        async with client.test_request_factory() as session:
            await team_service(session).ensure_personal_team(user_id)
            await session.commit()

    async def both():
        await personal_team()
        return await asyncio.gather(claim(), claim(), return_exceptions=True)

    outcomes = client.portal.call(both)

    opened = [one for one in outcomes if isinstance(one, uuid.UUID)]
    refused = [one for one in outcomes if not isinstance(one, uuid.UUID)]
    # The one that came second is told to ask again, and asking again finds the
    # project the first opened.
    assert len(opened) == 1 and len(refused) == 1, outcomes
    assert refused[0].retryable, refused[0]
    assert client.portal.call(claim) == opened[0]
    repositories = [
        path
        for path in Path(settings.workspace_root).iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(repositories) == 1, repositories
