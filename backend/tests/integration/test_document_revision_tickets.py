"""Who may write what into a live document (#3830 revision mode).

The rules, as a person would state them:

- a member who may change the document edits it;
- a mentor may not change the text but may suggest changes: their editor
  opens for suggestions only;
- someone who may only read it opens it read-only.
"""

import jwt

from tests.integration.conftest import _register, new_project, session_auth_headers

ALICE = session_auth_headers("alice")


def _invite(client, pid: str, handle: str, role: str) -> None:
    _register(client, handle)
    inv = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": handle, "role": role},
        headers=ALICE,
    )
    assert inv.status_code == 200, inv.text
    ok = client.post(
        f"/invitations/{inv.json()['data']['id']}/respond",
        json={"accept": True},
        headers=session_auth_headers(handle),
    )
    assert ok.status_code == 200, ok.text


def _ticket(client, doc: str, handle: str) -> dict:
    got = client.get(f"/documents/{doc}/ticket", headers=session_auth_headers(handle))
    assert got.status_code == 200, got.text
    data = got.json()["data"]
    claims = jwt.decode(data["ticket"], options={"verify_signature": False})
    assert (claims["ro"], claims["sg"]) == (data["read_only"], data["suggest_only"])
    return data


def _doc(client) -> tuple[str, str]:
    p = new_project(client, owner="alice")["id"]
    made = client.post(
        f"/projects/{p}/documents", json={"title": "开题报告"}, headers=ALICE
    )
    assert made.status_code == 200, made.text
    return p, made.json()["data"]["id"]


def test_a_member_edits_and_a_mentor_only_suggests(client):
    p, doc = _doc(client)
    _invite(client, p, "teacher", "mentor")
    _invite(client, p, "bob", "member")

    mentor = _ticket(client, doc, "teacher")
    assert (mentor["read_only"], mentor["suggest_only"]) == (False, True)
    member = _ticket(client, doc, "bob")
    assert (member["read_only"], member["suggest_only"]) == (False, False)


def test_a_mentor_without_the_comment_permission_only_reads(client):
    p, doc = _doc(client)
    _invite(client, p, "teacher", "mentor")
    roles = client.get(f"/projects/{p}/roles", headers=ALICE).json()["data"]["roles"]
    mentor = next(r for r in roles if r["template"] == "mentor")
    without = [g for g in mentor["permissions"] if g != "comment"]
    changed = client.patch(
        f"/projects/{p}/roles/{mentor['key']}",
        json={"permissions": without},
        headers=ALICE,
    )
    assert changed.status_code == 200, changed.text

    mentor = _ticket(client, doc, "teacher")
    assert (mentor["read_only"], mentor["suggest_only"]) == (True, False)
