"""A refusal carries the key of its sentence, so the reader's screen can say it
in the reader's language.

The Chinese sentence stays in ``error.message`` for every client that shows it
as it is (agents, the CLI); ``error.i18n`` names the catalog sentence and its
parameters. An error said in words the catalog does not have carries no key.
"""

from app.core.config import settings
from tests.integration.conftest import new_project, session_auth_headers

OWNER = "owner-1"


def _invite(client, bearer, project_id: str, handle: str, *, by: str = OWNER):
    return client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": handle},
        headers=bearer(by),
    )


def test_a_validation_refusal_carries_its_key(client, bearer):
    project_id = new_project(client, name="Demo", owner=OWNER)["id"]
    assert _invite(client, bearer, project_id, "alice").status_code == 200

    again = _invite(client, bearer, project_id, "alice")

    assert again.status_code == 422
    error = again.json()["error"]
    assert error["i18n"] == {"key": "invitePending", "params": {}}
    assert error["message"] == "已经邀请过这个人，正在等他答复"


def test_a_permission_refusal_carries_its_key(client, bearer):
    project_id = new_project(client, name="Demo", owner=OWNER)["id"]
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]["id"]

    answered = client.post(
        f"/invitations/{invitation}/respond",
        json={"accept": True},
        headers=bearer("mallory"),
    )

    assert answered.status_code == 403
    error = answered.json()["error"]
    assert error["i18n"] == {"key": "inviteNotYours", "params": {}}
    assert error["message"] == "只有被邀请的人能答复这张邀请"


def test_an_error_said_in_plain_words_carries_no_key(client, bearer):
    missing = "00000000-0000-0000-0000-000000000000"

    r = client.post(
        f"/invitations/{missing}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )

    assert r.status_code == 404
    assert "i18n" not in r.json()["error"]


def test_a_refusal_with_parameters_carries_them(client, bearer, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    project_id = new_project(client, name="Demo", owner=OWNER)["id"]
    room = client.post(
        "/topics",
        json={"project_id": project_id, "title": "周报"},
        headers=session_auth_headers(OWNER),
    ).json()["data"]["id"]
    method = {
        "name": "weekly-report",
        "title": "项目周报",
        "description": "把一周的项目进展整理成一页周报",
        "inputs": "本周的时间范围",
        "steps": "1. 列出本周完成的任务",
        "outputs": "一页 markdown",
    }
    assert client.post(f"/topics/{room}/skills", json=method).status_code == 200

    again = client.post(f"/topics/{room}/skills", json=method)

    assert again.status_code == 422
    error = again.json()["error"]
    assert error["i18n"] == {
        "key": "skillNameTaken",
        "params": {"name": "weekly-report"},
    }
    assert error["message"] == "这个项目里已经有叫「weekly-report」的工作方法"
