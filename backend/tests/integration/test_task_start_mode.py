"""A task starts one of two ways, and its project opens only when the claim is
approved.

- 直接开始 (``DIRECT``): clicking 开始做这道题 makes a claim already approved and
  opens its project at once; everyone hands in by the task's own deadline, and
  the task's places are counted (under the same lock as an approval) when the
  claim is made.
- 申请制 (``APPLY``): clicking 申请做这道题 makes a pending application and no
  project. Approval opens the project under the claim's team, and the claim's
  deadline is the task's 限时 days from then. A rejected application never has
  one.

Through the real HTTP routes, each request in its own transaction.
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.common.auth import verify_access_token
from app.domain.project.models import Project
from app.domain.task.models import TaskMembership
from app.domain.team.models import Team
from tests.conftest import seed_user
from tests.integration.test_claiming_twice import (
    _on_board,
    _real_requests,
    _shared_team,
    _slow_forge,
)
from tests.integration.test_starter_claims_at_once import _board
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    upload_root,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("upload_root")

DAY_MS = 86_400_000


def _closes() -> int:
    """A task deadline three weeks out, to the millisecond the API speaks."""
    return int(time.time() * 1000) + 21 * DAY_MS


def _student(client, board: dict) -> str:
    token = seed_user(client, f"student{uuid.uuid4().hex[:8]}")
    _on_board(client, board, token)
    return token


def _start(client, task_id: int, token: str, team_id: int | None = None):
    if team_id is None:
        return client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
    return client.post(
        f"/tasks/{task_id}/participations/team",
        json={"teamId": team_id},
        headers=_auth(token),
    )


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _decide(client, board: dict, task_id: int, participant: int, verdict: str):
    return client.patch(
        f"/tasks/{task_id}/participants/{participant}",
        json={"approved": verdict},
        headers=_auth(board["creator_token"]),
    )


def _page(client, task_id: int, token: str) -> dict:
    return _data(client.get(f"/tasks/{task_id}", headers=_auth(token)))["task"]


def _projects_of(client, task_id: int) -> list[tuple[str, int]]:
    async def read() -> list[tuple[str, int]]:
        async with client.test_factory() as session:
            rows = await session.execute(
                select(Project.id, Project.team_id).where(
                    Project.external_task_id == task_id
                )
            )
            return [(str(pid), team) for pid, team in rows]

    return client.portal.call(read)


def _approved_claims(client, task_id: int) -> int:
    async def count() -> int:
        async with client.test_factory() as session:
            return await session.scalar(
                select(func.count())
                .select_from(TaskMembership)
                .where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                    TaskMembership.approved == 0,
                )
            )

    return client.portal.call(count)


def _personal_team(client, token: str) -> int:
    user_id = verify_access_token(token).user_id

    async def read() -> int:
        async with client.test_factory() as session:
            return await session.scalar(
                select(Team.id).where(Team.personal_owner_user_id == user_id)
            )

    return client.portal.call(read)


# --- 直接开始 -----------------------------------------------------------------------


def test_starting_a_task_started_directly_opens_its_project_with_the_tasks_deadline(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    closes = _closes()
    task_id = _create_task(
        client, board, name="直接开始的题", startMode="DIRECT", deadline=closes
    )
    student = _student(client, board)

    started = _data(_start(client, task_id, student))

    assert started["participant"]["approved"] == "APPROVED"
    assert started["project"] is not None
    assert started["project"]["team_id"] == _personal_team(client, student)
    page = _page(client, task_id, student)
    assert page["startMode"] == "DIRECT"
    assert page["userDeadline"] == closes
    # 继续做: the task page points at the project the claim opened.
    assert page["myProject"]["id"] == started["project"]["id"]
    # Starting again opens the same one.
    assert _data(_start(client, task_id, student))["project"] == started["project"]
    assert len(_projects_of(client, task_id)) == 1


def test_a_full_task_started_directly_turns_the_next_person_away(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client, board, name="只有一个名额", startMode="DIRECT", participantLimit=1
    )
    first, second = _student(client, board), _student(client, board)

    assert _data(_start(client, task_id, first))["project"] is not None
    refused = _start(client, task_id, second)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
    assert _approved_claims(client, task_id) == 1
    assert len(_projects_of(client, task_id)) == 1
    assert _page(client, task_id, second)["myProject"] is None


def test_two_people_starting_at_once_on_the_last_place_get_one_of_it(
    client, monkeypatch
):
    """The place is counted and taken in one step, under the lock an approval
    takes, and released before the repository is made."""
    _real_requests(client, monkeypatch)
    _slow_forge(monkeypatch, 1)
    board = _board(client)
    task_id = _create_task(
        client, board, name="最后一个名额", startMode="DIRECT", participantLimit=1
    )
    students = [_student(client, board), _student(client, board)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda t: _start(client, task_id, t), students))

    assert sorted(r.status_code for r in responses) == [200, 403], [
        r.text for r in responses
    ]
    refused = next(r for r in responses if r.status_code == 403)
    assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
    assert _approved_claims(client, task_id) == 1
    assert len(_projects_of(client, task_id)) == 1


def test_moving_the_deadline_of_a_task_started_directly_moves_everyones(
    client, monkeypatch
):
    """Everyone hands in by the task's deadline; a claim the publisher gave a
    deadline of its own keeps it."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    closes = _closes()
    task_id = _create_task(
        client, board, name="改截止时间", startMode="DIRECT", deadline=closes
    )
    follows, own = _student(client, board), _student(client, board)
    _data(_start(client, task_id, follows))
    claim = _data(_start(client, task_id, own))["participant"]["id"]
    kept = closes + 2 * DAY_MS
    _data(
        client.patch(
            f"/tasks/{task_id}/participants/{claim}",
            json={"deadline": kept},
            headers=_auth(board["creator_token"]),
        )
    )

    moved = closes + 7 * DAY_MS
    _data(
        client.patch(
            f"/tasks/{task_id}",
            json={"deadline": moved},
            headers=_auth(board["creator_token"]),
        )
    )

    assert _page(client, task_id, follows)["userDeadline"] == moved
    assert _page(client, task_id, own)["userDeadline"] == kept


# --- 申请制 ------------------------------------------------------------------------


def test_an_application_opens_no_project_until_it_is_approved(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="申请制的题", defaultDeadline=10)
    student = _student(client, board)

    applied = _data(_start(client, task_id, student))

    assert applied["participant"]["approved"] == "NONE"
    assert applied["project"] is None
    assert _projects_of(client, task_id) == []
    page = _page(client, task_id, student)
    assert page["startMode"] == "APPLY"
    assert page["myProject"] is None
    # Asking again while it waits makes nothing either.
    assert _data(_start(client, task_id, student))["project"] is None
    assert _projects_of(client, task_id) == []

    approved_at = datetime.now(UTC)
    approved = _decide(client, board, task_id, applied["participant"]["id"], "APPROVED")
    assert approved.status_code == 200, approved.text

    projects = _projects_of(client, task_id)
    assert [team for _, team in projects] == [_personal_team(client, student)]
    page = _page(client, task_id, student)
    assert page["myProject"]["id"] == projects[0][0]
    # 限时 10 天, from approval.
    deadline = datetime.fromtimestamp(page["userDeadline"] / 1000, tz=UTC)
    assert abs(deadline - (approved_at + timedelta(days=10))) < timedelta(minutes=1)
    # 继续做 after approval goes to that same project.
    assert _data(_start(client, task_id, student))["project"]["id"] == projects[0][0]


def test_a_teams_application_opens_its_project_in_the_team_on_approval(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client,
        board,
        name="团队申请",
        submitterType="TEAM",
        maxTeamSize=5,
        defaultDeadline=14,
    )
    owner, mate = _student(client, board), seed_user(client, f"m{uuid.uuid4().hex[:8]}")
    team_id = _shared_team(client, owner, _on_board(client, board, mate))

    applied = _data(_start(client, task_id, mate, team_id))
    assert applied["project"] is None
    assert _projects_of(client, task_id) == []

    approved = _decide(client, board, task_id, applied["participant"]["id"], "APPROVED")
    assert approved.status_code == 200, approved.text

    projects = _projects_of(client, task_id)
    assert [team for _, team in projects] == [team_id]
    for token in (owner, mate):
        assert _page(client, task_id, token)["myProject"]["id"] == projects[0][0]


def test_a_rejected_application_opens_no_project(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="被拒的申请")
    student = _student(client, board)
    applied = _data(_start(client, task_id, student))

    rejected = _decide(
        client, board, task_id, applied["participant"]["id"], "DISAPPROVED"
    )

    assert rejected.status_code == 200, rejected.text
    assert _projects_of(client, task_id) == []
    assert _page(client, task_id, student)["myProject"] is None


def test_a_full_task_refuses_the_approval_and_opens_nothing(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="申请制满员", participantLimit=1)
    first, second = _student(client, board), _student(client, board)
    one = _data(_start(client, task_id, first))["participant"]["id"]
    two = _data(_start(client, task_id, second))["participant"]["id"]
    assert _decide(client, board, task_id, one, "APPROVED").status_code == 200

    refused = _decide(client, board, task_id, two, "APPROVED")

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
    assert len(_projects_of(client, task_id)) == 1
    assert _page(client, task_id, second)["myProject"] is None


def test_a_waiting_claim_keeps_the_project_an_earlier_release_opened(
    client, monkeypatch
):
    """Projects opened for applications before this rule stay; the task page
    offers 继续做 into them."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="早先开的项目")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    (opened, _), *_ = _projects_of(client, task_id)

    async def back_to_waiting() -> None:
        async with client.test_factory() as session:
            row = await session.get(TaskMembership, claim)
            row.approved = 2
            await session.commit()

    client.portal.call(back_to_waiting)

    page = _page(client, task_id, student)
    assert page["myProject"]["id"] == opened
    assert _data(_start(client, task_id, student))["project"] is None
    # Approved again, it opens the project it already has.
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    assert [pid for pid, _ in _projects_of(client, task_id)] == [opened]
