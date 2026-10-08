"""项目的 agent 凭证: 芝士 can act from outside the platform process.

芝士's identity already existed — ``cheese`` is a real user with an agent
binding, and it is seeded into every topic's roster. What did not exist was a
credential it could HOLD. The only agent credential was a per-turn scoped token
minted by the compute provider for one topic and dead when the turn ended, so
nothing running off-box could be 芝士: not a local agent, not a bot, not CI.

The credential these tests pin is the project's, not a member's. It names a
project and reaches every topic in it; it does not name a person, does not
derive its reach from one, and does not change when the person who issued it
changes role or leaves. Inside the project it is a member — the same reach a
member has, and refused where a member is refused.
"""

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.sandbox_auth import mint_project_agent_credential, mint_scoped_token
from tests.conftest import TEST_DATABASE_URL, seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

# --- helpers ------------------------------------------------------------------


def _steward(client, handle: str) -> dict[str, str]:
    """A real logged-in human — ``require_auth_user`` needs a numeric-sub token,
    which the handle-only helper deliberately does not mint."""
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _project(client, owner: str) -> str:
    r = post_project(client, json={"name": "P"}, owner=owner)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _topic(client, project_id: str, *, title: str, by: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _issue(client, project_id: str, headers: dict[str, str], **body) -> dict:
    return client.post(
        f"/projects/{project_id}/agent-credential", json=body, headers=headers
    )


def _issued_token(client, project_id: str, owner: str = "alice") -> str:
    r = _issue(client, project_id, _steward(client, owner))
    assert r.status_code == 200, r.text
    # This fixture explicitly grants ordinary project membership. Issuing a
    # credential itself does not grant a role (covered by test_agent_role_parity).
    # 看的是它有没有**自己那一行授权行**，不是名册上提没提到它：项目的队友本来就在
    # 名册上（带 source），而项目级写面那道闸问的是前者。
    handle = r.json()["data"]["agent_handle"]
    members = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    granted = {m["user_handle"] for m in members if "source" not in m}
    if handle not in granted:
        added = client.post(
            f"/projects/{project_id}/members",
            json={"user_handle": handle},
            headers=_steward(client, owner),
        )
        assert added.status_code == 200, added.text
    return r.json()["data"]["token"]


def _project_agent(client, project_id: str) -> str:
    """The project's own 芝士 — the participant a project credential acts as.

    Read off the project's agents rather than derived from its root room: the
    credential names an agent, and an agent's name comes from the agent.
    """
    rows = client.get(f"/projects/{project_id}/agents").json()["data"]["data"]
    (default,) = [row for row in rows if row["is_default"]]
    return default["seat_handle"]


def _cred(token: str) -> dict[str, str]:
    """How an off-platform agent presents it: the same header the sandbox uses,
    so every existing cheese-aware client works unchanged."""
    return {"X-Cheese-Token": token}


def _write(client, topic_id: str, token: str, content: str = "芝士写的"):
    """Write in the topic: a weekly note, which the response says the author
    of — these tests are about who a write is attributed to and where it is
    let through, not about what is written."""
    return client.post(
        f"/topics/{topic_id}/weekly", json={"body": content}, headers=_cred(token)
    )


def _acts_as_cheese(client, project_id: str, token: str) -> bool:
    """Whether ``token`` still authenticates, probed by the author it writes,
    always into a fresh topic."""
    tid = _topic(client, project_id, title="probe", by="alice")
    response = _write(client, tid, token)
    return response.status_code == 200 and response.json()["data"][
        "author"
    ] == _project_agent(client, project_id)


# --- 签发 ----------------------------------------------------------------------


def test_the_secret_is_returned_once_and_nowhere_else(client):
    """Issue is the only sight of the plaintext: nothing stores it, so a lost
    credential is re-issued (and the old ones revoked), never recovered."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")

    issued = _issue(client, pid, headers)
    assert issued.status_code == 200, issued.text
    token = issued.json()["data"]["token"]
    assert token.startswith("cxpa_")

    status = client.get(f"/projects/{pid}/agent-credential", headers=headers)
    assert status.status_code == 200
    assert "token" not in status.json()["data"]
    assert token not in status.text


def test_only_the_owner_or_a_team_admin_may_issue(client):
    """Handing the project's agent a credential is a decision about the project,
    so it takes someone who answers for the project. A plain member of its team
    cannot, and neither can somebody with no claim on it at all."""
    pid = _project(client, "alice")
    owner = _steward(client, "alice")

    join_project_team(client, pid, "bob")
    join_project_team(client, pid, "carol", admin=True)

    assert _issue(client, pid, owner).status_code == 200
    assert _issue(client, pid, _steward(client, "carol")).status_code == 200
    assert _issue(client, pid, _steward(client, "bob")).status_code == 403
    assert _issue(client, pid, _steward(client, "mallory")).status_code == 403


def test_member_agent_cannot_issue_another_credential(client):
    """Issuance requires an explicitly assigned management role."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    r = client.post(f"/projects/{pid}/agent-credential", json={}, headers=_cred(token))
    assert r.status_code == 403


def test_an_absurd_lifetime_is_refused(client):
    """Expiry is the one bound this credential does carry; an unbounded one
    would make revocation the only way it ever stops working."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")
    assert _issue(client, pid, headers, expiresInDays=0).status_code == 422
    assert _issue(client, pid, headers, expiresInDays=99999).status_code == 422


# --- 作用域: 整个项目, 只有这个项目 -----------------------------------------------


def test_one_credential_works_in_every_topic_of_its_project(client):
    """The point of widening the scope from a topic to a project: one credential
    an agent holds, not one per room that dies with the turn."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    first = _topic(client, pid, title="T1", by="alice")
    second = _topic(client, pid, title="T2", by="alice")

    for tid in (first, second):
        r = _write(client, tid, token, content=f"doc {tid}")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["author"] == _project_agent(client, pid)


def test_a_credential_is_refused_in_another_project(client):
    """Project-scoped means project-bounded. It is a 403, not a silent
    non-authentication: failing to authenticate would drop the caller into the
    handle fallback, which authorization reads as unauthenticated and lets
    through — presenting another project's credential would then beat presenting
    none at all."""
    mine = _project(client, "alice")
    theirs = _project(client, "bob")
    token = _issued_token(client, mine)
    their_topic = _topic(client, theirs, title="T", by="bob")

    r = _write(client, their_topic, token)
    assert r.status_code == 403, r.text


def test_a_forged_credential_is_not_a_credential(client):
    """The prefix is not the credential — the signature is."""
    pid = _project(client, "alice")
    tid = _topic(client, pid, title="T", by="alice")
    forged = f"cxpa_{uuid.uuid4().hex}.{uuid.uuid4().hex}"

    assert _write(client, tid, forged).status_code == 401


# --- 失效: 撤销 / 过期 -----------------------------------------------------------


def test_revoking_stops_it_on_the_very_next_request(client):
    """Revocation bumps the project's credential generation, so every credential
    issued so far stops verifying at once — no row to delete, no cache to wait
    for, and no way for one to survive the bump."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")
    token = _issued_token(client, pid)
    tid = _topic(client, pid, title="T", by="alice")

    assert _acts_as_cheese(client, pid, token)

    revoked = client.delete(f"/projects/{pid}/agent-credential", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["epoch"] == 1

    assert not _acts_as_cheese(client, pid, token)
    gated = client.post(
        f"/topics/{tid}/weekly", json={"body": "x"}, headers=_cred(token)
    )
    assert gated.status_code == 401


def test_revoking_retires_every_credential_at_once(client):
    """A project has ONE agent, so "revoke which one" is not a question the model
    can pose — the earlier credential must not outlive the bump either."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")
    older = _issued_token(client, pid)
    newer = _issued_token(client, pid)

    client.delete(f"/projects/{pid}/agent-credential", headers=headers)

    assert not _acts_as_cheese(client, pid, older)
    assert not _acts_as_cheese(client, pid, newer)
    assert _acts_as_cheese(client, pid, _issued_token(client, pid))


def test_an_expired_credential_is_refused(client):
    """Genuinely signed by this platform, for this project, at the current
    generation — and past its expiry, which is the only thing that matters."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")
    tid = _topic(client, pid, title="T", by="alice")
    epoch = client.get(f"/projects/{pid}/agent-credential", headers=headers).json()[
        "data"
    ]["epoch"]
    expired = mint_project_agent_credential(project_id=pid, epoch=epoch, ttl_s=-1)

    assert not _acts_as_cheese(client, pid, expired)
    gated = client.post(
        f"/topics/{tid}/weekly", json={"body": "x"}, headers=_cred(expired)
    )
    assert gated.status_code == 401


# --- 失效: 撤销与并发写 -----------------------------------------------------------


async def _settings_blob(project_id: str) -> str:
    """The whole settings blob, as a concurrent settings writer reads it: one
    value it will write back whole, whenever its transaction gets there."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT settings::text FROM projects WHERE id = :pid"),
                {"pid": project_id},
            )
            return row.scalar_one()
    finally:
        await engine.dispose()


async def _put_settings_blob(project_id: str, blob: str) -> None:
    """That writer's flush, on its own connection — exactly the other writer a
    revoke competes with in production."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE projects SET settings = CAST(:blob AS json) WHERE id = :pid"),
                {"pid": project_id, "blob": blob},
            )
    finally:
        await engine.dispose()


def test_revocation_outlives_a_settings_writer_that_flushes_late(client):
    """A write-back of the whole settings blob cannot revive what a revoke
    retired.

    Every settings writer reads the blob whole and writes it back whole, with no
    lock and no version, so one that read before a revoke landed and flushed
    after it is the ordinary case, not a rare one. The generation used to live in
    that blob: the late write carried the old one back, every credential the
    revoke had just retired verified again, and nothing logged a word."""
    pid = _project(client, "alice")
    headers = _steward(client, "alice")
    token = _issued_token(client, pid)
    assert _acts_as_cheese(client, pid, token)

    # What the other writer had read, before the revoke landed.
    stale = asyncio.run(_settings_blob(pid))

    revoked = client.delete(f"/projects/{pid}/agent-credential", headers=headers)
    assert revoked.status_code == 200, revoked.text

    # ...and what it flushes afterwards.
    asyncio.run(_put_settings_blob(pid, stale))

    assert not _acts_as_cheese(client, pid, token)


# --- 权限: 和这个项目的成员一样大 -------------------------------------------------


def test_it_reaches_the_cheese_write_surface_in_every_topic(client):
    """The gate used to demand a token naming THIS topic, which a project-level
    credential structurally cannot be — so issuing one without widening the gate
    would have bought nothing. The gate now resolves the topic's project and
    lets the project's own credential through."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    first = _topic(client, pid, title="T1", by="alice")
    second = _topic(client, pid, title="T2", by="alice")

    for tid in (first, second):
        r = client.post(
            f"/topics/{tid}/weekly",
            json={"body": "改用 asyncpg"},
            headers=_cred(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["author"].startswith("cheese")


def test_it_reaches_a_project_level_write(client):
    """Same widening on a write addressed to the project rather than a topic."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)

    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "title": "内测上线",
            "kind": "change_alert",
            "level": "light",
            "target_handle": "alice",
        },
        headers=_cred(token),
    )
    assert r.status_code == 200, r.text


def test_the_gate_still_refuses_another_project_s_credential(client):
    """Both route authorization and the execution gate enforce project scope."""
    mine = _project(client, "alice")
    theirs = _project(client, "bob")
    token = _issued_token(client, mine)
    their_topic = _topic(client, theirs, title="T", by="bob")

    topic_level = client.post(
        f"/topics/{their_topic}/weekly",
        json={"body": "x"},
        headers=_cred(token),
    )
    assert topic_level.status_code == 403

    project_level = client.post(
        f"/projects/{theirs}/alerts",
        json={
            "title": "别人的提醒",
            "kind": "change_alert",
            "level": "light",
            "target_handle": "bob",
        },
        headers=_cred(token),
    )
    assert project_level.status_code == 403


def test_it_is_a_member_not_a_lead(client):
    """ "As big as a member" is a ceiling as well as a floor. Inviting someone
    into the project decides who else reaches it, and a plain member cannot do it
    either — so refusing here is the model holding, not an exception to it."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)

    r = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": "mallory"},
        headers=_cred(token),
    )
    assert r.status_code == 403, r.text


# --- 留痕 ----------------------------------------------------------------------


def test_what_it_writes_is_filed_under_the_fixed_project_agent(client):
    """Writes use the credential-bound agent: a document of the project's, and
    a note in a topic."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    tid = _topic(client, pid, title="T", by="alice")
    room_agent = _project_agent(client, pid)

    doc = client.post(
        f"/projects/{pid}/documents",
        json={"title": "芝士写的", "content": "# 芝士写的"},
        headers=_cred(token),
    )
    assert doc.status_code == 200, doc.text
    history = client.get(
        f"/documents/{doc.json()['data']['id']}/history",
        headers=session_auth_headers("alice"),
    ).json()["data"]["versions"]
    assert [row["actor"] for row in history] == [room_agent]

    weekly = _write(client, tid, token, content="记一笔")
    assert weekly.json()["data"]["author"] == room_agent


# --- 不能改坏既有的路径 -----------------------------------------------------------


def test_a_per_turn_scoped_token_is_still_bound_to_its_own_topic(client):
    """The old credential keeps its old, narrower rule: widening applies to the
    new one only."""
    pid = _project(client, "alice")
    mine = _topic(client, pid, title="T1", by="alice")
    other = _topic(client, pid, title="T2", by="alice")
    per_turn = mint_scoped_token(project_id=pid, topic_id=mine)

    assert _write(client, mine, per_turn).status_code == 200
    assert _write(client, other, per_turn).status_code == 403


def test_a_tasks_own_session_takes_and_gives_back_the_heavy_lock(client):
    """A task is a conversation of its own, and the session working it holds a
    token naming the task: the gate finds the task's project through its
    channel, as for any conversation inside one."""
    pid = _project(client, "alice")
    room = _topic(client, pid, title="T", by="alice")
    task = client.post(
        f"/topics/{room}/tasks",
        json={"title": "Task"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    session = mint_scoped_token(project_id=pid, topic_id=task)

    taken = client.post(
        f"/topics/{task}/lock", json={"kind": "heavy"}, headers=_cred(session)
    )
    assert taken.status_code == 200, taken.text
    assert taken.json()["data"]["acquired"] is True
    given = client.post(
        f"/topics/{task}/unlock", json={"kind": "heavy"}, headers=_cred(session)
    )
    assert given.status_code == 200, given.text


def test_a_project_wide_per_turn_token_still_does_not_author_in_a_topic(client):
    """The git-http / LLM proxies mint topic-less scoped tokens. Those are
    capability tokens, not identity — widening the NEW credential must not have
    promoted them into one."""
    pid = _project(client, "alice")
    capability = mint_scoped_token(project_id=pid)

    assert not _acts_as_cheese(client, pid, capability)
