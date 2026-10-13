"""Refusals an admin page or a form shows carry their sentence's key, so the
screen says them in its reader's language.

A rule a validator enforces on a form a person fills in answers with that
rule's sentence and key, not the generic sentence `invalidRequestParameters`; a
malformed body nobody types still answers that generic one, key and all.
"""

import uuid

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_auth_headers
from tests.support.quoted_context import slide_quote

ADMIN = "admin-1"


def _project(client) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    response = post_project(client, json={"name": "Env", "owner_handle": "alice"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], headers


def test_a_non_admin_is_refused_with_a_key(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])

    r = client.get("/admin/integrations/feishu", headers=session_auth_headers("bob"))

    assert r.status_code == 403
    assert r.json()["error"]["i18n"] == {"key": "platformAdminRequired", "params": {}}
    assert r.json()["error"]["message"] == "需要平台管理员"


def test_saving_the_feishu_app_without_a_secret_is_refused_with_a_key(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])

    r = client.put(
        "/admin/integrations/feishu",
        json={"app_id": "cli_platform", "app_secret": "", "domain": "feishu"},
        headers=session_auth_headers(ADMIN),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"] == {
        "key": "feishuAppSecretRequired",
        "params": {},
    }


def test_a_reserved_environment_variable_says_which(client):
    project_id, headers = _project(client)

    r = client.put(
        f"/projects/{project_id}/environment",
        headers=headers,
        json={"variables": {"PATH": "/opt/bin"}},
    )

    assert r.status_code == 400, r.text
    error = r.json()["error"]
    assert error["message"] == "环境变量 PATH 是保留名，或者不是合法的变量名"
    assert error["i18n"] == {
        "key": "environmentVariableNameInvalid",
        "params": {"name": "PATH"},
    }


def test_a_quote_without_a_message_says_so(client):
    project_id, _ = _project(client)
    room = client.post(
        "/topics", json={"project_id": project_id, "title": "Room"}
    ).json()["data"]["id"]

    r = client.post(
        f"/topics/{room}/messages",
        headers=session_auth_headers("alice"),
        json={
            "content": "  ",
            "request_id": str(uuid.uuid4()),
            "quoted_context": slide_quote("引用文字"),
        },
    )

    assert r.status_code == 400, r.text
    assert r.json()["error"]["i18n"] == {"key": "quoteNeedsMessage", "params": {}}


def test_a_malformed_body_still_answers_the_generic_refusal(client):
    project_id, headers = _project(client)

    r = client.put(
        f"/projects/{project_id}/environment",
        headers=headers,
        json={"variables": "not a mapping"},
    )

    assert r.status_code == 400
    # A body the validator cannot read still answers with a key, so the screen
    # says it in the reader's language instead of the server's English.
    assert r.json()["error"]["message"] == "请求参数不合法"
    assert r.json()["error"]["i18n"] == {
        "key": "invalidRequestParameters",
        "params": {},
    }
