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

import pytest

from app.core import db as core_db
from app.domain.task import starter_jobs
from tests.conftest import seed_user
from tests.integration.conftest import create_approved_space
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    upload_root,  # noqa: F401
)
from tests.integration.test_task_starter_code import _main, _set_starter

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
