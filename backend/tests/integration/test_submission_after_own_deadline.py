"""A claim's own deadline is the last moment to hand a version in.

The publisher approves a claim with a deadline (approval time plus the
challenge's 提交期限) and can move it afterwards. Past it, the claim with nothing
in hand reads FAILED, so the submission endpoint refuses a new version and says
why; moving the deadline lets the participant hand in again. Editing a version
already handed in, on a challenge that allows it, is handing work in too and
follows the same rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@pytest.fixture
def claim(user_client: UserCreator, api_client: TestClient) -> dict:
    """A participant's approved claim on a resubmittable challenge."""
    creator = user_client.create_user()
    creator_token = user_client.login(api_client, creator.username, creator.password)
    participant = user_client.create_user()
    participant_token = user_client.login(
        api_client, participant.username, participant.password
    )

    resp = create_approved_space(
        api_client,
        json={
            "name": f"Own deadline ({unique_int()})",
            "intro": "一块板",
            "description": "交作业的截止时间",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]

    resp = api_client.post(
        "/tasks",
        json={
            "name": f"题 {unique_int()}",
            "intro": "题",
            "description": "题目说明 " * 10,
            "space": space["id"],
            "categoryId": space["defaultCategoryId"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 14,
            "deadline": _ms(datetime.now(UTC) + timedelta(days=30)),
            "submissionSchema": [{"prompt": "成果说明", "type": "TEXT"}],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(creator_token),
    )
    assert resp.status_code == 200, resp.text

    resp = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={},
        headers=_auth(participant_token),
    )
    assert resp.status_code == 200, resp.text
    participant_id = resp.json()["data"]["participant"]["id"]

    return {
        "task_id": task_id,
        "participant_id": participant_id,
        "creator_token": creator_token,
        "participant_token": participant_token,
    }


def _set_claim(api_client: TestClient, claim: dict, **body) -> None:
    resp = api_client.patch(
        f"/tasks/{claim['task_id']}/participants/{claim['participant_id']}",
        json=body,
        headers=_auth(claim["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _hand_in(api_client: TestClient, claim: dict, text: str):
    return api_client.post(
        f"/tasks/{claim['task_id']}/participants/{claim['participant_id']}/submissions",
        json=[{"text": text}],
        headers=_auth(claim["participant_token"]),
    )


def test_a_version_before_the_deadline_is_taken(
    api_client: TestClient, claim: dict
) -> None:
    _set_claim(
        api_client,
        claim,
        approved="APPROVED",
        deadline=_ms(datetime.now(UTC) + timedelta(days=14)),
    )

    resp = _hand_in(api_client, claim, "第一版")

    assert resp.status_code == 200, resp.text


def test_a_version_after_the_deadline_is_refused_and_says_why(
    api_client: TestClient, claim: dict
) -> None:
    _set_claim(
        api_client,
        claim,
        approved="APPROVED",
        deadline=_ms(datetime.now(UTC) - timedelta(hours=1)),
    )

    resp = _hand_in(api_client, claim, "晚交的一版")

    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["i18n"]["key"] == "submissionPastDeadline"
    assert "截止" in error["message"]
    listed = api_client.get(
        f"/tasks/{claim['task_id']}/participants/{claim['participant_id']}/submissions",
        headers=_auth(claim["participant_token"]),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["submissions"] == []


def test_after_a_first_version_the_deadline_still_closes_the_door(
    api_client: TestClient, claim: dict
) -> None:
    """A resubmittable challenge takes new versions only until the deadline."""
    _set_claim(
        api_client,
        claim,
        approved="APPROVED",
        deadline=_ms(datetime.now(UTC) + timedelta(days=14)),
    )
    assert _hand_in(api_client, claim, "第一版").status_code == 200
    _set_claim(
        api_client, claim, deadline=_ms(datetime.now(UTC) - timedelta(minutes=5))
    )

    resp = _hand_in(api_client, claim, "第二版")

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "submissionPastDeadline"


def test_moving_the_deadline_lets_the_participant_hand_in_again(
    api_client: TestClient, claim: dict
) -> None:
    _set_claim(
        api_client,
        claim,
        approved="APPROVED",
        deadline=_ms(datetime.now(UTC) - timedelta(hours=1)),
    )
    assert _hand_in(api_client, claim, "晚交的一版").status_code == 400

    _set_claim(api_client, claim, deadline=_ms(datetime.now(UTC) + timedelta(days=3)))
    resp = _hand_in(api_client, claim, "延期之后交的一版")

    assert resp.status_code == 200, resp.text


def _edit(api_client: TestClient, claim: dict, version: int, text: str):
    return api_client.patch(
        f"/tasks/{claim['task_id']}/participants/{claim['participant_id']}"
        f"/submissions/{version}",
        json=[{"text": text}],
        headers=_auth(claim["participant_token"]),
    )


def _first_version(api_client: TestClient, claim: dict) -> int:
    _set_claim(
        api_client,
        claim,
        approved="APPROVED",
        deadline=_ms(datetime.now(UTC) + timedelta(days=14)),
    )
    resp = _hand_in(api_client, claim, "第一版")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["version"]


def _texts(api_client: TestClient, claim: dict) -> str:
    listed = api_client.get(
        f"/tasks/{claim['task_id']}/participants/{claim['participant_id']}/submissions",
        headers=_auth(claim["participant_token"]),
    )
    assert listed.status_code == 200, listed.text
    return repr(listed.json()["data"]["submissions"])


def test_editing_a_version_before_the_deadline_is_taken(
    api_client: TestClient, claim: dict
) -> None:
    version = _first_version(api_client, claim)

    resp = _edit(api_client, claim, version, "改过的第一版")

    assert resp.status_code == 200, resp.text
    assert "改过的第一版" in _texts(api_client, claim)


def test_editing_a_version_after_the_deadline_is_refused_and_says_why(
    api_client: TestClient, claim: dict
) -> None:
    version = _first_version(api_client, claim)
    _set_claim(
        api_client, claim, deadline=_ms(datetime.now(UTC) - timedelta(minutes=5))
    )

    resp = _edit(api_client, claim, version, "截止后改的一版")

    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["i18n"]["key"] == "submissionPastDeadline"
    assert "截止" in error["message"]
    texts = _texts(api_client, claim)
    assert "第一版" in texts
    assert "截止后改的一版" not in texts


def test_moving_the_deadline_lets_the_participant_edit_again(
    api_client: TestClient, claim: dict
) -> None:
    version = _first_version(api_client, claim)
    _set_claim(
        api_client, claim, deadline=_ms(datetime.now(UTC) - timedelta(minutes=5))
    )
    assert _edit(api_client, claim, version, "截止后改的一版").status_code == 400

    _set_claim(api_client, claim, deadline=_ms(datetime.now(UTC) + timedelta(days=3)))
    resp = _edit(api_client, claim, version, "延期之后改的一版")

    assert resp.status_code == 200, resp.text
    assert "延期之后改的一版" in _texts(api_client, claim)


def test_a_claim_no_longer_approved_cannot_edit_its_version(
    api_client: TestClient, claim: dict
) -> None:
    version = _first_version(api_client, claim)
    _set_claim(api_client, claim, approved="DISAPPROVED")

    resp = _edit(api_client, claim, version, "撤销批准后改的一版")

    assert resp.status_code == 403, resp.text
