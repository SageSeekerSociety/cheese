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

import uuid

from app.core.sandbox_auth import mint_project_agent_credential, mint_scoped_token
from app.domain.identity.handles import looks_like_agent_handle, topic_agent_handle
from tests.conftest import seed_user

# --- helpers ------------------------------------------------------------------


def _steward(client, handle: str) -> dict[str, str]:
    """A real logged-in human — ``require_auth_user`` needs a numeric-sub token,
    which the handle-only helper deliberately does not mint."""
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _project(client, owner: str) -> str:
    r = client.post("/projects", json={"name": "P", "owner_handle": owner})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _topic(client, project_id: str, *, title: str, by: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": by},
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
    return r.json()["data"]["token"]


def _cred(token: str) -> dict[str, str]:
    """How an off-platform agent presents it: the same header the sandbox uses,
    so every existing cheese-aware client works unchanged."""
    return {"X-Cheese-Token": token}


def _write_doc(client, topic_id: str, token: str, content: str = "# 芝士写的"):
    return client.put(
        f"/topics/{topic_id}/doc", json={"content": content}, headers=_cred(token)
    )


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def _acts_as_cheese(client, project_id: str, token: str) -> bool:
    """Whether ``token`` still authenticates, probed by the author it writes.

    Always into a FRESH topic: editing a doc that already exists only replaces
    its content (``BlockRepository.update_content``), so the block keeps whoever
    authored it first — a probe on a reused topic would report the credential
    working long after it stopped.
    """
    tid = _topic(client, project_id, title="probe", by="alice")
    author = _write_doc(client, tid, token).json()["data"]["author"]
    return author == topic_agent_handle(uuid.UUID(tid))


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


def test_only_the_owner_or_a_lead_may_issue(client):
    """Handing the project's agent a credential is a decision about the project,
    so it takes someone who answers for the project. A plain member cannot, and
    neither can somebody with no claim on it at all."""
    pid = _project(client, "alice")
    owner = _steward(client, "alice")

    for handle, role in (("bob", "member"), ("carol", "lead")):
        added = client.post(
            f"/projects/{pid}/members",
            json={"user_handle": handle, "role": role},
            headers=owner,
        )
        assert added.status_code == 200, added.text

    assert _issue(client, pid, owner).status_code == 200
    assert _issue(client, pid, _steward(client, "carol")).status_code == 200
    assert _issue(client, pid, _steward(client, "bob")).status_code == 403
    assert _issue(client, pid, _steward(client, "mallory")).status_code == 403


def test_an_agent_credential_cannot_issue_another_one(client):
    """Otherwise revoking would not end the access — it would just be the
    previous key on a keyring the agent still holds."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    r = client.post(f"/projects/{pid}/agent-credential", json={}, headers=_cred(token))
    assert r.status_code == 401


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
        r = _write_doc(client, tid, token, content=f"# doc {tid}")
        assert r.status_code == 200, r.text
        # One credential, but each write is attributed to the room it landed in
        # — the credential names no 分身, so the room says who acted.
        assert r.json()["data"]["author"] == topic_agent_handle(uuid.UUID(tid))


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

    r = _write_doc(client, their_topic, token)
    assert r.status_code == 403, r.text


def test_a_forged_credential_is_not_a_credential(client):
    """The prefix is not the credential — the signature is."""
    pid = _project(client, "alice")
    tid = _topic(client, pid, title="T", by="alice")
    forged = f"cxpa_{uuid.uuid4().hex}.{uuid.uuid4().hex}"

    # Not authenticated at all: the write falls back to the pre-token path and
    # never lands as 芝士.
    author = _write_doc(client, tid, forged).json()["data"]["author"]
    assert author != topic_agent_handle(uuid.UUID(tid))
    assert not looks_like_agent_handle(author)
    gated = client.post(
        f"/topics/{tid}/decision", json={"decision": "x"}, headers=_cred(forged)
    )
    assert gated.status_code == 401


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
        f"/topics/{tid}/decision", json={"decision": "x"}, headers=_cred(token)
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
        f"/topics/{tid}/decision", json={"decision": "x"}, headers=_cred(expired)
    )
    assert gated.status_code == 401


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
            f"/topics/{tid}/decision",
            json={"decision": "改用 asyncpg"},
            headers=_cred(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["author_type"] == "ai"


def test_it_reaches_the_project_level_write_surface(client):
    """Same widening on the project-scoped half of the gated surface."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)

    r = client.post(
        f"/projects/{pid}/milestones",
        json={"title": "内测上线"},
        headers=_cred(token),
    )
    assert r.status_code == 200, r.text
    listed = client.get(f"/projects/{pid}/milestones").json()["data"]["data"]
    assert [m["title"] for m in listed] == ["内测上线"]


def test_the_gate_still_refuses_another_project_s_credential(client):
    """Widening the gate must not have flattened it. These routes have no
    authorization behind them — the gate IS the check — so a credential from
    another project has to die here."""
    mine = _project(client, "alice")
    theirs = _project(client, "bob")
    token = _issued_token(client, mine)
    their_topic = _topic(client, theirs, title="T", by="bob")

    topic_level = client.post(
        f"/topics/{their_topic}/decision",
        json={"decision": "x"},
        headers=_cred(token),
    )
    assert topic_level.status_code == 401

    project_level = client.post(
        f"/projects/{theirs}/milestones",
        json={"title": "别人的里程碑"},
        headers=_cred(token),
    )
    assert project_level.status_code == 401


def test_it_is_a_member_not_a_lead(client):
    """ "As big as a member" is a ceiling as well as a floor. Writing the project
    roster decides who else reaches the project, and a plain member cannot do it
    either — so refusing here is the model holding, not an exception to it."""
    pid = _project(client, "alice")
    token = _issued_token(client, pid)

    r = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "mallory", "role": "lead"},
        headers=_cred(token),
    )
    assert r.status_code == 403, r.text


# --- 留痕 ----------------------------------------------------------------------


def test_what_it_writes_is_filed_under_the_rooms_own_agent(client):
    """Not under the person who issued it, not under ``anonymous``, and not
    under the platform-wide ``cheese`` — that handle now names ONE agent, the
    project's default, which owns a memory pool. Filing every credential write
    there would put work the default agent never did under its name. A
    project-wide credential names no 分身 of its own, so the ROOM answers: the
    same 分身 a per-turn token in that room would have named.

    The conversation event the platform emits alongside says 芝士 rather than a
    mention chip, because the writer is recognised as the agent it is.

    ``author_type`` stays the route's structural value (a doc edit is ``human``,
    a decision is ``ai``): the column partitions what the next turn must respond
    to, not who wrote it. Identity lives in ``author``.
    """
    pid = _project(client, "alice")
    token = _issued_token(client, pid)
    tid = _topic(client, pid, title="T", by="alice")
    room_agent = topic_agent_handle(uuid.UUID(tid))

    doc = _write_doc(client, tid, token)
    assert doc.status_code == 200, doc.text
    assert doc.json()["data"]["author"] == room_agent
    assert doc.json()["data"]["author_type"] == "human"

    events = [b for b in _blocks(client, tid) if b["kind"] == "event"]
    edit_events = [b for b in events if "编辑了文档" in b["content"]]
    assert edit_events, _blocks(client, tid)
    assert edit_events[-1]["author"] == room_agent
    assert edit_events[-1]["content"] == "芝士 编辑了文档"

    decision = client.post(
        f"/topics/{tid}/decision",
        json={"decision": "记一笔"},
        headers=_cred(token),
    )
    assert decision.json()["data"]["author_type"] == "ai"


# --- 不能改坏既有的路径 -----------------------------------------------------------


def test_a_per_turn_scoped_token_is_still_bound_to_its_own_topic(client):
    """The old credential keeps its old, narrower rule: widening applies to the
    new one only."""
    pid = _project(client, "alice")
    mine = _topic(client, pid, title="T1", by="alice")
    other = _topic(client, pid, title="T2", by="alice")
    per_turn = mint_scoped_token(project_id=pid, topic_id=mine)

    assert _write_doc(client, mine, per_turn).status_code == 200
    assert _write_doc(client, other, per_turn).status_code == 403


def test_a_project_wide_per_turn_token_still_does_not_author_in_a_topic(client):
    """The git-http / LLM proxies mint topic-less scoped tokens. Those are
    capability tokens, not identity — widening the NEW credential must not have
    promoted them into one."""
    pid = _project(client, "alice")
    capability = mint_scoped_token(project_id=pid)

    assert not _acts_as_cheese(client, pid, capability)
