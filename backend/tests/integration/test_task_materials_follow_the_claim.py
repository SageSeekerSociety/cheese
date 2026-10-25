"""A task's materials follow the one claim a project came from.

Every project made from a task comes from one claim — a person's, or one
team's — and lives in that claim's team: a person's claim in their own name,
a team's claim in that team. While that claim is live and approved, everyone
in the project and the project's agents read the read-only `题目/` folder,
and the claimants download the materials from the task page. While it waits,
after it was rejected, withdrawn or removed, nobody reads `题目/` there; a
claim the reader holds through some other team does not open this project's
folder.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.project.models import Project
from app.domain.task import package
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from tests.integration.conftest import CreatedUser, UserCreator, unique_int
from tests.integration.test_task_files import (
    _add,
    _auth,
    _create_task,
    _download,
    _member_of,
    _new_board,
    _raw_in_project,
    _top_paths,
    upload_root,  # noqa: F401  (fixture)
)

DATA = b"a,b\n"


def _claim_as_user(api_client: TestClient, task_id: int, token: str) -> tuple[int, str]:
    """A person's claim, and the project it opened."""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["participant"]["id"], data["project"]["id"]


def _claim_as_team(
    api_client: TestClient, task_id: int, team_id: int, token: str
) -> tuple[int, str]:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/team",
        json={"teamId": team_id},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["participant"]["id"], data["project"]["id"]


def _decide(
    api_client: TestClient, board: dict, task_id: int, participant: int, verdict: str
) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{participant}",
        json={"approved": verdict},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _withdraw(api_client: TestClient, task_id: int, participant: int, token: str):
    """By the claimant (withdrawn) or by the publisher (removed): the same route."""
    resp = api_client.delete(
        f"/tasks/{task_id}/participants/{participant}", headers=_auth(token)
    )
    assert resp.status_code == 204, resp.text


def _team(api_client: TestClient, token: str) -> tuple[int, str]:
    name = f"队 {unique_int(10000000, 99999999)}"
    resp = api_client.post(
        "/teams",
        json={"name": name, "intro": "x", "description": "x", "avatarId": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["team"]["id"], name


def _join(db_session, _portal, team_id: int, user: CreatedUser) -> None:
    """Someone joins a team (how they joined is not what these tests are about)."""

    async def join() -> None:
        now = datetime.now(UTC)
        db_session.add(
            TeamUserRelation(
                team_id=team_id,
                user_id=user.user_id,
                role=TeamMemberRole.MEMBER,
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()

    _portal.call(join)


def _agents_told(db_session, _portal, project_id: str) -> bool:
    """Whether the project's agents are given the task section (and `题目/`)."""

    async def read() -> str | None:
        project = await db_session.get(Project, uuid.UUID(project_id))
        return await package.section_for_project(db_session, project)

    return _portal.call(read) is not None


def _reads_folder(api_client: TestClient, project_id: str, token: str) -> bool:
    shown = "题目" in _top_paths(api_client, project_id, token)
    raw = _raw_in_project(api_client, project_id, token, "数据.csv")
    assert shown == (raw.status_code == 200), raw.text
    if not shown:
        assert raw.status_code == 403
        assert (
            raw.json()["error"]["i18n"]["key"] == "libraryTaskFolderNeedsApprovedClaim"
        )
    return shown


def _downloads(api_client: TestClient, task_id: int, token: str) -> bool:
    resp = _download(api_client, task_id, token, "数据.csv")
    assert resp.status_code in (200, 403), resp.text
    return resp.status_code == 200


# --- a person's claim -------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["pending", "approved", "rejected", "withdrawn", "removed"]
)
def test_a_persons_project_reads_the_task_only_while_their_claim_is_approved(
    state: str,
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
    _portal,
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name=f"个人题 {state}")
    _add(api_client, task_id, board["creator_token"], "数据.csv", DATA)
    _, token = _member_of(user_client, api_client, board)
    participant, project_id = _claim_as_user(api_client, task_id, token)

    if state != "pending":
        _decide(
            api_client,
            board,
            task_id,
            participant,
            "DISAPPROVED" if state == "rejected" else "APPROVED",
        )
    if state == "withdrawn":
        _withdraw(api_client, task_id, participant, token)
    if state == "removed":
        _withdraw(api_client, task_id, participant, board["creator_token"])

    approved = state == "approved"
    assert _reads_folder(api_client, project_id, token) is approved
    assert _downloads(api_client, task_id, token) is approved
    assert _agents_told(db_session, _portal, project_id) is approved


# --- a team's claim ---------------------------------------------------------------


@pytest.mark.parametrize("state", ["pending", "approved", "rejected", "removed"])
def test_everyone_in_a_teams_project_reads_the_task_only_while_its_claim_is_approved(
    state: str,
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
    _portal,
):
    """The team's owner, a teammate who was there when it applied, and a
    teammate who joined afterwards all read the same thing."""
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name=f"团队题 {state}", submitterType="TEAM"
    )
    _add(api_client, task_id, board["creator_token"], "数据.csv", DATA)
    _, owner_token = _member_of(user_client, api_client, board)
    team_id, _ = _team(api_client, owner_token)
    early, early_token = _member_of(user_client, api_client, board)
    _join(db_session, _portal, team_id, early)
    participant, project_id = _claim_as_team(api_client, task_id, team_id, owner_token)

    if state != "pending":
        _decide(
            api_client,
            board,
            task_id,
            participant,
            "DISAPPROVED" if state == "rejected" else "APPROVED",
        )
    late, late_token = _member_of(user_client, api_client, board)
    _join(db_session, _portal, team_id, late)
    if state == "removed":
        _withdraw(api_client, task_id, participant, board["creator_token"])

    approved = state == "approved"
    for token in (owner_token, early_token, late_token):
        assert _reads_folder(api_client, project_id, token) is approved
        assert _downloads(api_client, task_id, token) is approved
    assert _agents_told(db_session, _portal, project_id) is approved


def test_a_claim_through_another_team_does_not_open_this_projects_task_folder(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
    _portal,
):
    """Someone in two teams that both applied: the team whose claim was approved
    gives them the materials and its own project's `题目/`; the other team's
    project shows none while that team's claim waits — to them, to its other
    members, and to the agents of a project they made there."""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="两个团队", submitterType="TEAM")
    _add(api_client, task_id, board["creator_token"], "数据.csv", DATA)
    both, both_token = _member_of(user_client, api_client, board)
    _, other_token = _member_of(user_client, api_client, board)
    approved_team, _ = _team(api_client, both_token)
    waiting_team, _ = _team(api_client, other_token)
    approved_claim, approved_project = _claim_as_team(
        api_client, task_id, approved_team, both_token
    )
    _, waiting_project = _claim_as_team(api_client, task_id, waiting_team, other_token)
    _decide(api_client, board, task_id, approved_claim, "APPROVED")
    _join(db_session, _portal, waiting_team, both)
    resp = api_client.post(
        "/projects",
        json={
            "name": "在等的团队的另一个项目",
            "external_task_id": task_id,
            "team_id": waiting_team,
        },
        headers=_auth(both_token),
    )
    assert resp.status_code == 200, resp.text
    made_there = resp.json()["data"]["id"]

    assert _downloads(api_client, task_id, both_token)
    assert _reads_folder(api_client, approved_project, both_token)
    for project_id in (waiting_project, made_there):
        assert not _reads_folder(api_client, project_id, both_token)
        assert not _reads_folder(api_client, project_id, other_token)
        assert not _agents_told(db_session, _portal, project_id)


# --- which team a project made from a task goes in -------------------------------


def test_a_project_from_a_persons_claim_goes_in_their_own_name(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="个人题建项目")
    _, token = _member_of(user_client, api_client, board)
    participant, first = _claim_as_user(api_client, task_id, token)
    _decide(api_client, board, task_id, participant, "APPROVED")
    team_id, _ = _team(api_client, token)

    resp = api_client.post(
        "/projects",
        json={"name": "挂到团队", "external_task_id": task_id, "team_id": team_id},
        headers=_auth(token),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "challengeProjectInOwnName"

    own = api_client.get(f"/projects/{first}", headers=_auth(token)).json()["data"]
    for team in (None, own["team_id"]):
        resp = api_client.post(
            "/projects",
            json={"name": "自己名下", "external_task_id": task_id, "team_id": team},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["team_id"] == own["team_id"]


def test_a_project_from_a_teams_claim_goes_in_that_team(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="团队题建项目", submitterType="TEAM")
    _, token = _member_of(user_client, api_client, board)
    claimed, claimed_name = _team(api_client, token)
    other, _ = _team(api_client, token)
    _claim_as_team(api_client, task_id, claimed, token)

    for team in (other, None):
        resp = api_client.post(
            "/projects",
            json={"name": "别的团队", "external_task_id": task_id, "team_id": team},
            headers=_auth(token),
        )
        assert resp.status_code == 403, resp.text
        error = resp.json()["error"]["i18n"]
        assert error["key"] == "challengeProjectInClaimTeam"
        assert error["params"] == {"team": claimed_name}

    resp = api_client.post(
        "/projects",
        json={"name": "申请的团队", "external_task_id": task_id, "team_id": claimed},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team_id"] == claimed


def test_without_a_live_claim_no_project_is_made_from_the_task(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="没申请的题")
    _, token = _member_of(user_client, api_client, board)
    resp = api_client.post(
        "/projects",
        json={"name": "没申请", "external_task_id": task_id},
        headers=_auth(token),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "challengeProjectClaimFirst"
