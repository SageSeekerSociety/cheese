"""A document's mini-apps: who may run them, who may write them, and what the
platform's browser decides before a version is kept.

Whoever may read the document an app belongs to may read its code and pictures,
wherever the block is found — the key alone opens nothing. Only 芝士 writes a
version. A version that fails when the platform's browser runs it is not kept,
and the writer is told why; when no browser can be had, it is kept unchecked.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_delegated_credential, mint_scoped_token
from app.domain.web_snippet import check
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
CODE = "<div id='out'></div><script>kit.h('p', null, '试算')</script>"


@pytest.fixture
def browser(monkeypatch):
    """The platform's browser, as each test needs it to answer."""
    answers: list = []

    async def run(code: str, shell: int):
        answer = answers.pop(0) if answers else check.Ran(heights={400: 300, 720: 260})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(check, "run", run)
    return answers


def _project(client) -> tuple[str, str, dict]:
    """dave's project with a library document alice wrote; alice and bob are
    on its team, mallory is not. Returns the project, the document and the
    credential 芝士 works in the project with."""
    pid, doc, token, _, _ = _project_with_room(client)
    return pid, doc, token


def _project_with_room(client) -> tuple[str, str, dict, str, str]:
    """``_project``, with the room 芝士 works in and its seat there."""
    for who in ("alice", "bob", "dave", "mallory"):
        seed_user(client, who)
    pid = post_project(client, json={"name": "P"}, owner="dave").json()["data"]["id"]
    for handle in ("alice", "bob"):
        join_project_team(client, pid, handle)
    made = client.post(
        f"/projects/{pid}/documents",
        json={"title": "方案", "content": "正文\n"},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text
    room = client.post(
        "/topics",
        json={"project_id": pid, "title": "频道"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    rows = client.get(
        f"/topics/{room}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    [seat] = [m["member_handle"] for m in rows if m["agent"]]
    # A room's session reaches across its project, the library included.
    token = mint_scoped_token(
        project_id=pid, topic_id=room, agent_handle=seat, access_scope="project"
    )
    return (
        pid,
        made.json()["data"]["id"],
        {"Authorization": f"Bearer {token}"},
        room,
        seat,
    )


def _save(client, doc: str, agent: dict, code: str = CODE):
    return client.post(
        f"/documents/{doc}/apps", json={"code": code, "note": "第一版"}, headers=agent
    )


def test_whoever_reads_the_document_runs_its_app_and_nobody_else(client, browser):
    _, doc, agent = _project(client)
    saved = _save(client, doc, agent)
    assert saved.status_code == 200, saved.text
    key = saved.json()["data"]["key"]

    for reader in ("alice", "bob", "dave"):
        read = client.get(
            f"/apps/{key}/versions/1", headers=session_auth_headers(reader)
        )
        assert read.status_code == 200, read.text
        assert read.json()["data"]["code"] == CODE
    # The key alone opens nothing: mallory cannot read the document.
    for path in (f"/apps/{key}", f"/apps/{key}/versions/1"):
        refused = client.get(path, headers=session_auth_headers("mallory"))
        assert refused.status_code in (403, 404)
        assert CODE not in refused.text


def test_a_person_cannot_write_an_apps_code(client, browser):
    _, doc, agent = _project(client)
    refused = _save(client, doc, session_auth_headers("alice"))
    assert refused.status_code == 403
    key = _save(client, doc, agent).json()["data"]["key"]
    changed = client.post(
        f"/apps/{key}/versions",
        json={"code": "<p>改过</p>"},
        headers=session_auth_headers("alice"),
    )
    assert changed.status_code == 403
    versions = client.get(f"/apps/{key}", headers=session_auth_headers("alice"))
    assert [v["version"] for v in versions.json()["data"]["versions"]] == [1]


def test_code_that_fails_in_the_browser_is_not_kept_and_the_writer_is_told_why(
    client, browser
):
    _, doc, agent = _project(client)
    key = _save(client, doc, agent).json()["data"]["key"]

    browser.append(check.Refused("ReferenceError: chart is not defined"))
    refused = client.post(
        f"/apps/{key}/versions",
        json={"code": "<script>chart()</script>"},
        headers=agent,
    )
    assert refused.status_code == 422
    assert "chart is not defined" in refused.json()["error"]["message"]
    versions = client.get(f"/apps/{key}", headers=session_auth_headers("alice"))
    assert [v["version"] for v in versions.json()["data"]["versions"]] == [1]

    browser.append(check.Refused("ReferenceError: x is not defined"))
    assert _save(client, doc, agent, "<script>x()</script>").status_code == 422


def test_with_no_browser_to_run_it_a_version_is_kept_unchecked(client, browser):
    _, doc, agent = _project(client)
    browser.append(None)
    saved = _save(client, doc, agent)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["checked"] is False
    key = saved.json()["data"]["key"]
    read = client.get(f"/apps/{key}/versions/1", headers=session_auth_headers("bob"))
    assert read.json()["data"]["shots"] == []


def test_a_new_version_leaves_the_old_one_as_it_was(client, browser):
    _, doc, agent = _project(client)
    key = _save(client, doc, agent).json()["data"]["key"]
    second = client.post(
        f"/apps/{key}/versions", json={"code": "<p>第二版</p>"}, headers=agent
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["version"] == 2

    alice = session_auth_headers("alice")
    assert (
        client.get(f"/apps/{key}/versions/1", headers=alice).json()["data"]["code"]
        == CODE
    )
    assert (
        client.get(f"/apps/{key}/versions/2", headers=alice).json()["data"]["code"]
        == "<p>第二版</p>"
    )


def test_the_pictures_taken_when_it_was_kept_are_its_readers(client, browser):
    _, doc, agent = _project(client)
    browser.append(
        check.Ran(
            heights={400: 300, 720: 260}, shots={"400-light": PNG, "400-dark": PNG}
        )
    )
    key = _save(client, doc, agent).json()["data"]["key"]

    seen = client.get(
        f"/apps/{key}/versions/1/shot",
        params={"width": 400, "theme": "dark"},
        headers=session_auth_headers("bob"),
    )
    assert seen.status_code == 200, seen.text
    assert seen.content == PNG
    refused = client.get(
        f"/apps/{key}/versions/1/shot",
        params={"width": 400, "theme": "dark"},
        headers=session_auth_headers("mallory"),
    )
    assert refused.status_code in (403, 404)
    assert refused.content != PNG


def test_its_writer_may_have_the_pictures_back_with_the_save(client, browser):
    """For the AI teammate to look at what it made before it moves on: the
    light pictures, by width, only when asked for."""
    import base64

    _, doc, agent = _project(client)
    shots = {"400-light": PNG, "720-light": PNG, "400-dark": PNG, "720-dark": PNG}
    browser.append(check.Ran(heights={400: 300, 720: 260}, shots=shots))
    asked = client.post(
        f"/documents/{doc}/apps",
        params={"pictures": "true"},
        json={"code": CODE, "note": "第一版"},
        headers=agent,
    )
    assert asked.status_code == 200, asked.text
    pictures = asked.json()["data"]["pictures"]
    assert set(pictures) == {"400", "720"}
    assert base64.b64decode(pictures["720"]) == PNG

    browser.append(check.Ran(heights={400: 300, 720: 260}, shots=shots))
    plain = _save(client, doc, agent)
    assert "pictures" not in plain.json()["data"]


def test_a_version_made_because_someone_asked_is_the_teammates_own(client, browser):
    """芝士 answering someone on the document writes the app as itself: the
    version records and serves no one as having asked for it."""
    pid, doc, _, room, seat = _project_with_room(client)
    asked = mint_delegated_credential(
        user_id=None,
        handle="bob",
        agent=seat,
        project_id=pid,
        topic_id=room,
        work=str(uuid.uuid4()),
        read_only=False,
        ttl_s=300,
    )

    saved = _save(client, doc, {"Authorization": f"Bearer {asked}"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["author"] == seat
    assert "requested_by" not in saved.json()["data"]
