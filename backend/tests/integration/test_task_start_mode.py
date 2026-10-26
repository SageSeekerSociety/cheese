"""A task starts one of two ways, and 开始 is 用这道题新建项目: a claim's
project is made only when its claimant starts it.

- 直接开始 (``DIRECT``, the default for a new task): clicking 开始做这道题 makes
  a claim already approved and starts it at once; the task's deadline, when it
  has one, is everyone's, and the task's places are counted (under the same
  lock as an approval) when the claim is made.
- 申请制 (``APPLY``): clicking 申请做这道题 makes a pending application.
  Approval sets its deadline (the task's 限时 days from then) and makes no
  project; the student's 开始做这道题 afterwards does, under the claim's team.
  A rejected application never has one.

Mentor consent is asked when starting, never when applying.

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
from app.domain.project import forge
from app.domain.project.models import Project
from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import TaskMembership
from app.domain.team.models import Team
from app.domain.user.models import User
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


def _apply_task(client, board: dict, name: str, **more) -> int:
    return _create_task(client, board, name=name, startMode="APPLY", **more)


def _start_agreeing(client, task_id: int, token: str, team_id: int | None = None):
    body: dict = {"agreeMentors": True}
    if team_id is None:
        return client.post(
            f"/tasks/{task_id}/participations/user", json=body, headers=_auth(token)
        )
    return client.post(
        f"/tasks/{task_id}/participations/team",
        json={**body, "teamId": team_id},
        headers=_auth(token),
    )


def test_an_approved_application_has_no_project_until_the_student_starts_it(
    client, monkeypatch
):
    """开始 is 用这道题新建项目: approval makes no project; the student's
    开始做这道题 after it does, and the deadline still counts from approval."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "申请制的题", defaultDeadline=10)
    student = _student(client, board)

    applied = _data(_start(client, task_id, student))
    assert applied["participant"]["approved"] == "NONE"
    assert applied["project"] is None
    page = _page(client, task_id, student)
    assert page["startMode"] == "APPLY"
    assert page["myProject"] is None

    approved_at = datetime.now(UTC)
    approved = _decide(client, board, task_id, applied["participant"]["id"], "APPROVED")
    assert approved.status_code == 200, approved.text

    assert _projects_of(client, task_id) == []
    assert _page(client, task_id, student)["myProject"] is None

    started = _data(_start(client, task_id, student))["project"]

    projects = _projects_of(client, task_id)
    assert projects == [(started["id"], _personal_team(client, student))]
    page = _page(client, task_id, student)
    assert page["myProject"]["id"] == started["id"]
    # 限时 10 天, from approval, not from starting.
    deadline = datetime.fromtimestamp(page["userDeadline"] / 1000, tz=UTC)
    assert abs(deadline - (approved_at + timedelta(days=10))) < timedelta(minutes=1)
    # 继续做 goes to that same project.
    assert _data(_start(client, task_id, student))["project"]["id"] == started["id"]
    assert len(_projects_of(client, task_id)) == 1


def test_a_teams_approved_application_is_started_by_a_member_into_the_team(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(
        client, board, "团队申请", submitterType="TEAM", maxTeamSize=5
    )
    owner, mate = _student(client, board), seed_user(client, f"m{uuid.uuid4().hex[:8]}")
    team_id = _shared_team(client, owner, _on_board(client, board, mate))
    applied = _data(_start(client, task_id, mate, team_id))
    approved = _decide(client, board, task_id, applied["participant"]["id"], "APPROVED")
    assert approved.status_code == 200, approved.text
    assert _projects_of(client, task_id) == []

    started = _data(_start(client, task_id, mate, team_id))["project"]

    assert _projects_of(client, task_id) == [(started["id"], team_id)]
    for token in (owner, mate):
        assert _page(client, task_id, token)["myProject"]["id"] == started["id"]


def test_a_rejected_application_opens_no_project(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "被拒的申请")
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
    task_id = _apply_task(client, board, "申请制满员", participantLimit=1)
    first, second = _student(client, board), _student(client, board)
    one = _data(_start(client, task_id, first))["participant"]["id"]
    two = _data(_start(client, task_id, second))["participant"]["id"]
    assert _decide(client, board, task_id, one, "APPROVED").status_code == 200

    refused = _decide(client, board, task_id, two, "APPROVED")

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
    assert _projects_of(client, task_id) == []
    assert _page(client, task_id, second)["myProject"] is None


def test_a_full_task_takes_no_new_application(client, monkeypatch):
    """Once approved claims fill the places nobody can apply: the application
    could never be approved. Applications already waiting stay."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "满了不收申请", participantLimit=1)
    first, waiting, late = (_student(client, board) for _ in range(3))
    one = _data(_start(client, task_id, first))["participant"]["id"]
    held = _data(_start(client, task_id, waiting))["participant"]
    assert _decide(client, board, task_id, one, "APPROVED").status_code == 200

    refused = _start(client, task_id, late)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
    again = _data(_start(client, task_id, waiting))["participant"]
    assert (again["id"], again["approved"]) == (held["id"], "NONE")


def test_a_waiting_claim_with_an_earlier_releases_project_waits_then_continues(
    client, monkeypatch
):
    """Projects an earlier release made for applications still waiting stay.
    While the claim waits the page shows it waiting, with nothing to continue;
    approved, it continues into that project and no second one is made."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "早先开的项目")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    opened = _data(_start(client, task_id, student))["project"]["id"]

    async def back_to_waiting() -> None:
        async with client.test_factory() as session:
            row = await session.get(TaskMembership, claim)
            row.approved = 2
            await session.commit()

    client.portal.call(back_to_waiting)

    assert _page(client, task_id, student)["myProject"] is None
    assert _data(_start(client, task_id, student))["project"] is None
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    assert _page(client, task_id, student)["myProject"]["id"] == opened
    assert _data(_start(client, task_id, student))["project"]["id"] == opened
    assert [pid for pid, _ in _projects_of(client, task_id)] == [opened]


# --- what starting agrees to, who sets the deadline, and never a dead end --------


def _handle(client, token: str) -> str:
    user_id = verify_access_token(token).user_id

    async def read() -> str:
        async with client.test_factory() as session:
            return (await session.get(User, user_id)).username

    return client.portal.call(read)


def _reads_project(client, project_id: str, token: str) -> int:
    return client.get(f"/projects/{project_id}", headers=_auth(token)).status_code


def test_the_mentors_are_agreed_to_when_starting_not_when_applying(client, monkeypatch):
    """Applying asks nothing about mentors; starting does, and the project
    seats the mentors agreed to then."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    teacher = _handle(client, board["creator_token"])
    task_id = _apply_task(client, board, "有导师的申请制", mentors=[teacher])
    student = _student(client, board)

    applied = _start(client, task_id, student)
    assert applied.status_code == 200, applied.text
    claim = applied.json()["data"]["participant"]["id"]
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200

    unagreed = _start(client, task_id, student)
    assert unagreed.status_code == 400, unagreed.text
    assert unagreed.json()["error"]["i18n"]["key"] == "mentorConsentRequired"
    assert _projects_of(client, task_id) == []

    started = _data(_start_agreeing(client, task_id, student))["project"]
    assert _reads_project(client, started["id"], board["creator_token"]) == 200


def test_a_mentor_added_after_the_start_is_not_seated(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="开始后才加导师", startMode="DIRECT")
    student = _student(client, board)
    started = _data(_start(client, task_id, student))["project"]

    _data(
        client.patch(
            f"/tasks/{task_id}",
            json={"mentors": [_handle(client, board["creator_token"])]},
            headers=_auth(board["creator_token"]),
        )
    )

    assert _reads_project(client, started["id"], board["creator_token"]) == 403
    # 继续做 asks nothing it did not ask when the project was made.
    assert _data(_start(client, task_id, student))["project"] == started


def test_a_teacher_enrolling_a_student_makes_the_claim_and_the_student_starts_it(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="老师替学生报上", startMode="DIRECT")
    student = _student(client, board)
    member = verify_access_token(student).user_id

    enrolled = _data(
        client.post(
            f"/tasks/{task_id}/participants",
            params={"member": member},
            json={},
            headers=_auth(board["creator_token"]),
        )
    )

    assert enrolled["participant"]["approved"] == "APPROVED"
    assert enrolled["project"] is None
    assert _projects_of(client, task_id) == []
    started = _data(_start(client, task_id, student))["project"]
    assert _projects_of(client, task_id) == [
        (started["id"], _personal_team(client, student))
    ]


@pytest.mark.parametrize("route", ["participations/user", "participants"])
def test_a_starter_does_not_pick_their_own_deadline(client, monkeypatch, route):
    """On a task started directly the deadline is the task's; only the
    publisher sets anyone's own, on the roster."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    closes = _closes()
    task_id = _create_task(
        client, board, name="自己定截止", startMode="DIRECT", deadline=closes
    )
    student = _student(client, board)

    started = client.post(
        f"/tasks/{task_id}/{route}",
        json={"deadline": closes + 100 * DAY_MS},
        headers=_auth(student),
    )

    assert started.status_code == 200, started.text
    assert _page(client, task_id, student)["userDeadline"] == closes


def _forge_down(monkeypatch):
    """The forge stops answering; returns how to bring it back."""
    real = forge.prepare_repository

    async def unreachable(*args, **kwargs):
        raise forge.ForgeUnreachableError("forge down")

    monkeypatch.setattr(forge, "prepare_repository", unreachable)
    return lambda: monkeypatch.setattr(forge, "prepare_repository", real)


def test_a_start_whose_project_failed_makes_it_when_started_again(client, monkeypatch):
    """The forge fails after the claim is committed: the page still offers
    开始做这道题, and starting again makes the project."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="仓库没建成", startMode="DIRECT")
    student = _student(client, board)
    back = _forge_down(monkeypatch)
    failed = _start(client, task_id, student)
    assert failed.status_code == 503, failed.text
    assert _projects_of(client, task_id) == []
    assert _page(client, task_id, student)["myProject"] is None
    back()

    started = _data(_start(client, task_id, student))["project"]

    assert _page(client, task_id, student)["myProject"]["id"] == started["id"]


def _status(client, task_id: int) -> str:
    async def read() -> str:
        async with client.test_factory() as session:
            return await session.scalar(
                select(TaskMembership.completion_status).where(
                    TaskMembership.task_id == task_id
                )
            )

    return client.portal.call(read)


def _sweep(client) -> None:
    async def run() -> None:
        async with client.test_factory() as session:
            await check_and_fail_expired_deadlines(session)

    client.portal.call(run)


def test_moving_a_deadline_later_reopens_a_claim_the_sweep_failed(client, monkeypatch):
    """A claim's completion is derived again whenever its deadline moves: by
    the task's deadline on a task started directly, or by the publisher's
    own deadline for one claim."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client, board, name="延期", startMode="DIRECT", deadline=_closes()
    )
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]

    def move_task(at: int) -> None:
        _data(
            client.patch(
                f"/tasks/{task_id}",
                json={"deadline": at},
                headers=_auth(board["creator_token"]),
            )
        )

    move_task(int(time.time() * 1000) - DAY_MS)
    _sweep(client)
    assert _status(client, task_id) == "FAILED"
    move_task(_closes())
    assert _status(client, task_id) == "NOT_SUBMITTED"

    move_task(int(time.time() * 1000) - DAY_MS)
    _sweep(client)
    assert _status(client, task_id) == "FAILED"
    _data(
        client.patch(
            f"/tasks/{task_id}/participants/{claim}",
            json={"deadline": _closes()},
            headers=_auth(board["creator_token"]),
        )
    )
    assert _status(client, task_id) == "NOT_SUBMITTED"


def test_a_task_started_directly_may_have_no_deadline(client, monkeypatch):
    """No deadline means nobody has one; a deadline set later is everyone's."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client, board, name="随意做的题", startMode="DIRECT", deadline=None
    )
    student = _student(client, board)

    started = _data(_start(client, task_id, student))

    assert started["project"] is not None
    assert _page(client, task_id, student)["userDeadline"] is None
    closes = _closes()
    _data(
        client.patch(
            f"/tasks/{task_id}",
            json={"deadline": closes},
            headers=_auth(board["creator_token"]),
        )
    )
    assert _page(client, task_id, student)["userDeadline"] == closes


def test_a_new_task_starts_directly_unless_its_publisher_says_otherwise(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)

    task_id = _create_task(client, board, name="没说怎么开始")

    assert _page(client, task_id, board["creator_token"])["startMode"] == "DIRECT"


def _set_mode(client, board: dict, task_id: int, mode: str):
    return client.patch(
        f"/tasks/{task_id}",
        json={"startMode": mode},
        headers=_auth(board["creator_token"]),
    )


def test_how_a_task_starts_is_fixed_once_anyone_holds_a_claim(client, monkeypatch):
    """A live claim (approved or waiting) fixes the mode; a rejected one does
    not count."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "有人申请了")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]

    refused = _set_mode(client, board, task_id, "DIRECT")

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskStartModeLocked"
    assert _page(client, task_id, student)["startMode"] == "APPLY"
    # Saying the mode it already has changes nothing and is not refused.
    assert _set_mode(client, board, task_id, "APPLY").status_code == 200
    assert _decide(client, board, task_id, claim, "DISAPPROVED").status_code == 200
    assert _set_mode(client, board, task_id, "DIRECT").status_code == 200


def _apply_again(client, task_id: int, token: str) -> int:
    return _data(_start(client, task_id, token))["participant"]["id"]


@pytest.mark.parametrize("verdict", ["APPROVED", "NONE"])
def test_a_rejected_application_with_a_newer_one_cannot_be_decided_again(
    client, monkeypatch, verdict
):
    """The publisher decides the newer application; the rejected one is
    history. Undoing a rejection with no newer one beside it still works."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "又申请了一次")
    student = _student(client, board)
    old = _apply_again(client, task_id, student)
    assert _decide(client, board, task_id, old, "DISAPPROVED").status_code == 200
    newer = _apply_again(client, task_id, student)
    assert newer != old

    refused = _decide(client, board, task_id, old, verdict)

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "claimHasNewerApplication"
    alone = _student(client, board)
    only = _apply_again(client, task_id, alone)
    assert _decide(client, board, task_id, only, "DISAPPROVED").status_code == 200
    assert _decide(client, board, task_id, only, verdict).status_code == 200


# --- races, the retired way in, and the deadline ---------------------------------


def _paused_after(monkeypatch, name: str, during):
    """Replace ``claims.<name>`` with the real check followed, the first time
    it is called, by a pause in which ``during()`` runs on another thread for
    up to two seconds: a request landing between the check and the write that
    follows it. Each returned future says whether it finished inside the pause
    (``finished_in_pause``)."""
    import asyncio

    from app.domain.task import claims

    real = getattr(claims, name)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    landed: list[concurrent.futures.Future] = []

    async def paused(*args, **kwargs):
        result = await real(*args, **kwargs)
        if landed:
            return result
        future = pool.submit(during)
        landed.append(future)
        for _ in range(20):
            if future.done():
                break
            await asyncio.sleep(0.1)
        future.finished_in_pause = future.done()  # type: ignore[attr-defined]
        return result

    monkeypatch.setattr(claims, name, paused)
    return landed


def _claims_of(client, task_id: int) -> list[tuple[int, int]]:
    async def read() -> list[tuple[int, int]]:
        async with client.test_factory() as session:
            rows = await session.execute(
                select(TaskMembership.member_id, TaskMembership.approved).where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                )
            )
            return [(m, a) for m, a in rows]

    return client.portal.call(read)


def _mode(client, task_id: int) -> str:
    async def read() -> str:
        from app.domain.task.models import Task

        async with client.test_factory() as session:
            return (await session.get(Task, task_id)).start_mode

    return client.portal.call(read)


def test_an_application_landing_during_a_change_of_mode_is_made_under_the_mode_that_stays(
    client, monkeypatch
):
    """The change of mode and a first claim do not interleave: either the
    change is refused because the claim is there, or the claim is made under
    the new mode. Never a waiting application on a task that starts directly."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "改开始方式时有人申请")
    student = _student(client, board)
    landed = _paused_after(
        monkeypatch, "has_live_claim", lambda: _start(client, task_id, student)
    )

    changed = _set_mode(client, board, task_id, "DIRECT")

    [claim] = [r.result(timeout=10) for r in landed]
    assert changed.status_code in (200, 400), changed.text
    assert claim.status_code == 200, claim.text
    expected = {"DIRECT": 0, "APPLY": 2}[_mode(client, task_id)]
    assert [a for _, a in _claims_of(client, task_id)] == [expected]


@pytest.mark.parametrize("verdict", ["APPROVED", "NONE"])
@pytest.mark.parametrize("by_member", [False, True])
def test_a_re_application_landing_while_an_old_rejection_is_undone_leaves_one_live_claim(
    client, monkeypatch, verdict, by_member
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "撤回拒绝时又申请")
    student = _student(client, board)
    old = _data(_start(client, task_id, student))["participant"]
    assert _decide(client, board, task_id, old["id"], "DISAPPROVED").status_code == 200
    landed = _paused_after(
        monkeypatch, "has_newer_claim", lambda: _start(client, task_id, student)
    )

    decided = (
        client.patch(
            f"/tasks/{task_id}/participants",
            params={"member": old["memberId"]},
            json={"approved": verdict},
            headers=_auth(board["creator_token"]),
        )
        if by_member
        else _decide(client, board, task_id, old["id"], verdict)
    )

    [again] = [r.result(timeout=10) for r in landed]
    assert decided.status_code in (200, 400), decided.text
    assert again.status_code in (200, 409), again.text
    live = [a for _, a in _claims_of(client, task_id) if a != 1]
    assert len(live) == 1, live


def test_an_old_page_making_a_project_from_a_task_is_told_to_start_it(
    client, monkeypatch
):
    """A page from before starting was the way in still sends the task with a
    new project: it is refused with what to do, and no project is made."""
    from tests.integration.conftest import post_project

    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(client, board, name="旧页面建项目", startMode="DIRECT")

    refused = post_project(
        client, json={"name": "旧页面", "external_task_id": task_id}, owner="old-page"
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "projectFromTaskByStarting"

    async def count() -> int:
        async with client.test_factory() as session:
            return await session.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.owner_handle == "old-page")
            )

    assert client.portal.call(count) == 0


def _past() -> int:
    return int(time.time() * 1000) - DAY_MS


def _move_task_deadline(client, board: dict, task_id: int, at: int) -> None:
    _data(
        client.patch(
            f"/tasks/{task_id}",
            json={"deadline": at},
            headers=_auth(board["creator_token"]),
        )
    )


def test_nobody_starts_or_applies_once_the_task_has_closed(client, monkeypatch):
    _real_requests(client, monkeypatch)
    board = _board(client)
    direct = _create_task(client, board, name="截止了的直接开始", startMode="DIRECT")
    applied = _apply_task(client, board, "截止了的申请制")
    early, late = _student(client, board), _student(client, board)
    started = _data(_start(client, direct, early))["project"]
    for task_id in (direct, applied):
        _move_task_deadline(client, board, task_id, _past())

    for task_id in (direct, applied):
        refused = _start(client, task_id, late)
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["i18n"]["key"] == "taskClosedForStart"
    assert [m for m, _ in _claims_of(client, applied)] == []
    # Someone who started before it closed still continues into their project.
    assert _data(_start(client, direct, early))["project"] == started


def test_an_application_approved_after_signups_close_is_started_until_its_own_deadline(
    client, monkeypatch
):
    """On a task applied for, the task's deadline is 申请截止: it closes
    applying, not the approved claimant's start."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "申请截止后才通过")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]
    _move_task_deadline(client, board, task_id, _past())
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200

    started = _data(_start(client, task_id, student))["project"]

    assert _projects_of(client, task_id) == [
        (started["id"], _personal_team(client, student))
    ]
    assert _page(client, task_id, student)["myProject"]["id"] == started["id"]


def test_an_application_approved_after_signups_close_is_not_started_after_its_own_deadline(
    client, monkeypatch
):
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "申请截止后通过，自己的也过了")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]
    _move_task_deadline(client, board, task_id, _past())
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    _data(
        client.patch(
            f"/tasks/{task_id}/participants/{claim}",
            json={"deadline": _past()},
            headers=_auth(board["creator_token"]),
        )
    )

    refused = _start(client, task_id, student)

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "claimDeadlinePassedForStart"
    assert _projects_of(client, task_id) == []


def _only_claim(client, task_id: int) -> int:
    async def read() -> int:
        async with client.test_factory() as session:
            return await session.scalar(
                select(TaskMembership.id).where(TaskMembership.task_id == task_id)
            )

    return client.portal.call(read)


def test_a_direct_claim_is_not_started_after_the_task_closes_even_with_a_later_own_deadline(
    client, monkeypatch
):
    """On a task started directly the task's deadline is everyone's: a claim
    whose project was never made is not started once it passes, even when
    the publisher gave that claim a later deadline of its own."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _create_task(
        client, board, name="直接开始，整道题截止了", startMode="DIRECT"
    )
    student = _student(client, board)
    back = _forge_down(monkeypatch)
    assert _start(client, task_id, student).status_code == 503
    back()
    claim = _only_claim(client, task_id)
    _move_task_deadline(client, board, task_id, _past())
    _data(
        client.patch(
            f"/tasks/{task_id}/participants/{claim}",
            json={"deadline": _closes()},
            headers=_auth(board["creator_token"]),
        )
    )

    refused = _start(client, task_id, student)

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskClosedForStart"
    assert _projects_of(client, task_id) == []


def test_an_approved_application_is_not_started_after_its_own_deadline(
    client, monkeypatch
):
    """Starting then would only make a project already failed."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "自己的截止过了")
    student = _student(client, board)
    claim = _data(_start(client, task_id, student))["participant"]["id"]
    assert _decide(client, board, task_id, claim, "APPROVED").status_code == 200
    _data(
        client.patch(
            f"/tasks/{task_id}/participants/{claim}",
            json={"deadline": _past()},
            headers=_auth(board["creator_token"]),
        )
    )

    refused = _start(client, task_id, student)

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "claimDeadlinePassedForStart"
    assert _projects_of(client, task_id) == []


@pytest.mark.parametrize("verdict", ["APPROVED", "NONE"])
@pytest.mark.parametrize("by_member", [False, True])
def test_a_re_application_turned_away_while_the_old_one_is_decided_says_so(
    client, monkeypatch, verdict, by_member
):
    """Nothing is being created while the publisher decides the old
    application: the re-application that has to wait is told the application
    is being handled, not that a project is being made."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "处理旧申请时又申请")
    student = _student(client, board)
    old = _data(_start(client, task_id, student))["participant"]
    assert _decide(client, board, task_id, old["id"], "DISAPPROVED").status_code == 200
    landed = _paused_after(
        monkeypatch, "has_newer_claim", lambda: _start(client, task_id, student)
    )

    decided = (
        client.patch(
            f"/tasks/{task_id}/participants",
            params={"member": old["memberId"]},
            json={"approved": verdict},
            headers=_auth(board["creator_token"]),
        )
        if by_member
        else _decide(client, board, task_id, old["id"], verdict)
    )

    [again] = [r.result(timeout=10) for r in landed]
    assert decided.status_code == 200, decided.text
    assert again.status_code == 409, again.text
    assert again.json()["error"]["i18n"]["key"] == "claimBeingChanged"
    assert _projects_of(client, task_id) == []


def test_two_applications_to_one_task_do_not_wait_for_each_other(client, monkeypatch):
    """Reading the start mode holds it against a change, not against other
    claims: a second student's application goes through while the first is
    still being written."""
    _real_requests(client, monkeypatch)
    board = _board(client)
    task_id = _apply_task(client, board, "两人同时申请")
    first, second = _student(client, board), _student(client, board)
    landed = _paused_after(
        monkeypatch, "read_mode_for_claim", lambda: _start(client, task_id, second)
    )

    applied = _start(client, task_id, first)

    [other] = [r.result(timeout=10) for r in landed]
    assert applied.status_code == 200, applied.text
    assert other.status_code == 200, other.text
    # It finished inside the first one's pause, rather than after it.
    assert landed[0].finished_in_pause
    assert sorted(a for _, a in _claims_of(client, task_id)) == [2, 2]
