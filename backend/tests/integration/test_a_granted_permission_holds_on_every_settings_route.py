"""A permission a project grants a role holds on every route that asks for it:
a mentor whose role was granted 「编辑设置」 changes the project's settings —
its address and which tiers may run as well as its default model — and a mentor
without it changes none of them."""

from tests.integration.conftest import new_project, session_auth_headers
from tests.integration.test_document_revision_tickets import _invite

ALICE = session_auth_headers("alice")


def _grant_mentors(client, pid: str, *, edit_settings: bool) -> None:
    roles = client.get(f"/projects/{pid}/roles", headers=ALICE).json()["data"]["roles"]
    role = next(r for r in roles if r["template"] == "mentor")
    mentor = [g for g in role["permissions"] if g != "edit_settings"]
    if edit_settings:
        mentor.append("edit_settings")
    changed = client.patch(
        f"/projects/{pid}/roles/{role['key']}",
        json={"permissions": mentor},
        headers=ALICE,
    )
    assert changed.status_code == 200, changed.text


def _settings_changed_by(client, pid: str, handle: str) -> list[int]:
    as_them = session_auth_headers(handle)
    return [
        client.put(
            f"/projects/{pid}/slug", json={"slug": f"p-{pid[:8]}"}, headers=as_them
        ).status_code,
        client.put(
            f"/projects/{pid}/tier-policy",
            json={"allowed_tiers": None},
            headers=as_them,
        ).status_code,
        client.put(
            f"/projects/{pid}/default-model", json={"model": None}, headers=as_them
        ).status_code,
    ]


def test_a_mentor_granted_edit_settings_changes_every_setting(client):
    pid = new_project(client, owner="alice")["id"]
    _invite(client, pid, "teacher", "mentor")
    _grant_mentors(client, pid, edit_settings=True)

    assert _settings_changed_by(client, pid, "teacher") == [200, 200, 200]


def test_a_mentor_without_it_changes_none(client):
    pid = new_project(client, owner="alice")["id"]
    _invite(client, pid, "teacher", "mentor")
    _grant_mentors(client, pid, edit_settings=False)

    assert _settings_changed_by(client, pid, "teacher") == [403, 403, 403]
