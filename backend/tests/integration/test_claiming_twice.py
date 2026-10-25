"""Claiming a task you (or your team) already hold is not an error (#4767 §二).

Through the real HTTP routes, on real request transactions:

- asking again answers with the claim you hold and its project;
- two requests at the same moment leave one claim and one project: the second
  is told at once that the project is being created (retryable), and asking
  again then gets the project;
- no sequence of these reaches a server error.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.common.auth import verify_access_token
from app.core import db as core_db
from app.domain.project import forge
from app.domain.task.models import TaskMembership
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from tests.conftest import seed_user
from tests.integration.test_starter_claims_at_once import _board, _create_task
from tests.integration.test_task_files import (
    _auth,
    upload_root,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("upload_root")


def _on_board(client, board: dict, token: str) -> int:
    user_id = verify_access_token(token).user_id
    response = client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user_id},
        headers=_auth(board["creator_token"]),
    )
    assert response.status_code == 201, response.text
    return user_id


def _real_requests(client, monkeypatch) -> None:
    """Each request in its own transaction, with the deployment's lock timeout."""
    from app.main import app

    monkeypatch.setattr(core_db, "async_session_factory", client.test_request_factory)
    monkeypatch.setitem(app.dependency_overrides, core_db.get_db, core_db.get_db)


def _slow_forge(monkeypatch, seconds: float) -> None:
    for name in ("prepare_repository", "provision_repository"):
        quick = getattr(forge, name, None)
        if quick is None:
            continue

        async def slow(*args, _quick=quick, **kwargs):
            await asyncio.sleep(seconds)
            return await _quick(*args, **kwargs)

        monkeypatch.setattr(forge, name, slow)


def _live_claims(client, task_id: int) -> int:
    async def count() -> int:
        async with client.test_factory() as session:
            return await session.scalar(
                select(func.count())
                .select_from(TaskMembership)
                .where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                )
            )

    return client.portal.call(count)


def _claim(client, task_id: int, token: str, team_id: int | None = None):
    if team_id is None:
        return client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
    return client.post(
        f"/tasks/{task_id}/participations/team",
        json={"teamId": team_id},
        headers=_auth(token),
    )


def _at_once(client, task_id: int, tokens: list[str], team_id: int | None = None):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tokens)) as pool:
        return list(pool.map(lambda t: _claim(client, task_id, t, team_id), tokens))


def _shared_team(client, owner: str, member_id: int) -> int:
    response = client.post(
        "/teams",
        json={"name": f"队 {uuid.uuid4().hex[:6]}", "intro": "队", "avatarId": 1},
        headers=_auth(owner),
    )
    assert response.status_code == 201, response.text
    team_id = response.json()["data"]["team"]["id"]

    async def join() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:
            session.add(
                TeamUserRelation(
                    team_id=team_id,
                    user_id=member_id,
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    client.portal.call(join)
    return team_id


def _project(response) -> str:
    assert response.status_code == 200, response.text
    return response.json()["data"]["project"]["id"]


def test_claiming_a_task_you_hold_again_answers_with_its_project(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="再领一次")
    student = seed_user(client, f"student{uuid.uuid4().hex[:8]}")
    _on_board(client, board, student)

    first = _project(_claim(client, task_id, student))
    again = _project(_claim(client, task_id, student))

    assert again == first
    assert _live_claims(client, task_id) == 1


def test_one_person_claiming_twice_at_once_gets_one_project(client, monkeypatch):
    _real_requests(client, monkeypatch)
    _slow_forge(monkeypatch, 2)
    board = _board(client)
    task_id = _create_task(client, board, name="一个人同时点两次")
    student = seed_user(client, f"student{uuid.uuid4().hex[:8]}")
    _on_board(client, board, student)

    responses = _at_once(client, task_id, [student, student])

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409], [r.text for r in responses]
    busy = next(r for r in responses if r.status_code == 409).json()["error"]
    assert busy["retryable"] is True
    assert busy["i18n"]["key"] == "claimInProgress"
    opened = _project(next(r for r in responses if r.status_code == 200))
    assert _project(_claim(client, task_id, student)) == opened
    assert _live_claims(client, task_id) == 1


def test_two_teammates_claiming_at_once_get_one_claim_and_one_project(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    _slow_forge(monkeypatch, 2)
    board = _board(client)
    task_id = _create_task(
        client, board, name="两个队友同时领", submitterType="TEAM", maxTeamSize=5
    )
    owner = seed_user(client, f"owner{uuid.uuid4().hex[:8]}")
    mate = seed_user(client, f"mate{uuid.uuid4().hex[:8]}")
    _on_board(client, board, owner)
    team_id = _shared_team(client, owner, _on_board(client, board, mate))

    responses = _at_once(client, task_id, [owner, mate], team_id)

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409], [r.text for r in responses]
    opened = _project(next(r for r in responses if r.status_code == 200))
    # Both teammates, asking again, get the team's project.
    assert _project(_claim(client, task_id, owner, team_id)) == opened
    assert _project(_claim(client, task_id, mate, team_id)) == opened
    assert _live_claims(client, task_id) == 1


def _duplicate(client, claim_id: int) -> int:
    """Seed what nothing in the database stops yet: a second live claim of the
    same member and task. The original is approved and older; the duplicate is
    a newer pending application. Returns the approved claim's id."""

    async def seed() -> int:
        async with client.test_factory() as session:
            original = await session.get(TaskMembership, claim_id)
            assert original is not None
            original.approved = 0
            later = datetime.now(UTC)
            session.add(
                TaskMembership(
                    task_id=original.task_id,
                    member_id=original.member_id,
                    is_team=original.is_team,
                    approved=2,
                    email="",
                    phone="",
                    completion_status=original.completion_status,
                    created_at=later,
                    updated_at=later,
                    pitch="",
                )
            )
            await session.commit()
            return original.id

    return client.portal.call(seed)


def _claimed(response) -> int:
    assert response.status_code == 200, response.text
    return response.json()["data"]["participant"]["id"]


def test_a_person_holding_two_live_claims_still_gets_the_approved_one(
    client, monkeypatch
):
    """Duplicate claims already in the database never reach a server error:
    the task still opens, and claiming again answers with the approved claim
    and its project."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="重复的两份领取")
    student = seed_user(client, f"student{uuid.uuid4().hex[:8]}")
    _on_board(client, board, student)
    first = _claim(client, task_id, student)
    opened = _project(first)
    approved = _duplicate(client, _claimed(first))

    shown = client.get(f"/tasks/{task_id}", headers=_auth(student))
    again = _claim(client, task_id, student)

    assert shown.status_code == 200, shown.text
    assert _claimed(again) == approved
    assert _project(again) == opened


def test_a_team_holding_two_live_claims_still_gets_the_approved_one(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client, board, name="队里重复的两份领取", submitterType="TEAM", maxTeamSize=5
    )
    owner = seed_user(client, f"owner{uuid.uuid4().hex[:8]}")
    mate = seed_user(client, f"mate{uuid.uuid4().hex[:8]}")
    _on_board(client, board, owner)
    team_id = _shared_team(client, owner, _on_board(client, board, mate))
    first = _claim(client, task_id, owner, team_id)
    opened = _project(first)
    approved = _duplicate(client, _claimed(first))

    for token in (owner, mate):
        again = _claim(client, task_id, token, team_id)
        assert _claimed(again) == approved
        assert _project(again) == opened
        shown = client.get(f"/tasks/{task_id}", headers=_auth(token))
        assert shown.status_code == 200, shown.text
