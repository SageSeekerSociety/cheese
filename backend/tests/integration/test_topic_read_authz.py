"""Room reads require a verified member identity.

The harness supplies the explicit global development credential by default;
that trusted override is tested separately from anonymous access. Requests
with a participant identity still undergo normal membership checks.
"""

from tests.integration.conftest import session_auth_headers


def _project_with_a_secret(client) -> tuple[str, str]:
    """A project owned by alice, whose root topic holds one sensitive line."""
    p = client.post("/projects", json={"name": "薪资", "owner_handle": "alice"}).json()[
        "data"
    ]
    tid = p["root_topic_id"]
    r = client.post(
        f"/topics/{tid}/decision",
        json={"decision": "机密：下季度裁员名单"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return p["id"], tid


def test_an_outsider_cannot_read_the_conversation(client):
    """The one that actually leaked. `/blocks` carries the messages verbatim."""
    _, tid = _project_with_a_secret(client)
    r = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("mallory"))
    assert r.status_code == 403, r.text


def test_an_outsider_cannot_read_the_topic_header(client):
    """The title alone is worth denying — 「薪资讨论」 tells you plenty."""
    _, tid = _project_with_a_secret(client)
    r = client.get(f"/topics/{tid}", headers=session_auth_headers("mallory"))
    assert r.status_code == 403, r.text


def test_a_member_still_reads_both(client):
    """The half that must NOT change. A guard that also locks out the owner is
    not a fix, it is an outage."""
    _, tid = _project_with_a_secret(client)
    head = client.get(f"/topics/{tid}", headers=session_auth_headers("alice"))
    assert head.status_code == 200, head.text

    body = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("alice"))
    assert body.status_code == 200, body.text
    assert any("裁员名单" in (b["content"] or "") for b in body.json()["data"]["data"])


def test_explicit_development_credential_can_read(client):
    """The harness carries a trusted credential; this is not anonymous access."""
    _, tid = _project_with_a_secret(client)
    assert client.get(f"/topics/{tid}").status_code == 200
    assert client.get(f"/topics/{tid}/blocks").status_code == 200
