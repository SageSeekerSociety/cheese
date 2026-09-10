"""归属跟推进者走: a sub-topic belongs to whoever drove the turn it came out of.

A room stalls; someone else picks it up; 芝士 splits a sub-topic out of THEIR
turn. The child used to inherit the parent room's owner regardless — which is not
merely wrong bookkeeping: the child ends in an accept card, so the card landed on
someone who had already stopped working on this, and the work stalled a second
time. `cheese split` cannot supply the answer itself (it runs under the 分身's own
`cheese-<hex12>` handle, and letting an agent name the driver would be forgeable
anyway), so the endpoint reads it off the runner's live turn record.

The original requester is not written out of the history: they come back as
`Co-authored-by:` on the PR and on the squash commit. Which is also why that
trailer stopped appearing on ordinary single-person rooms — it used to name the
commit's own author, and a room has one git identity.
"""

import asyncio
import uuid

from app.api.deps import get_work_runner
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_token


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


class _FakeClient:
    opened: list[dict] = []

    def __init__(self, owner: str, repo: str, tokens, **_):
        pass

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        as_user_token: str | None = None,
    ) -> dict:
        type(self).opened.append({"body": body, "as_user_token": as_user_token})
        return {"number": 7, "html_url": "https://github.com/acme/widgets/pull/7"}


def _github_world(monkeypatch, *, connected: dict[str, tuple[str, str]]) -> None:
    """A GitHub the platform can push to. `connected` maps a handle to the
    (numeric id, login) of the account they linked — both halves matter: the id
    is what GitHub matches a no-reply address on."""
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    _FakeClient.opened = []

    async def _fake_tokens_for_project(_project_id, _session):
        return _FakeTokens()

    async def _fake_user_token(_session, handle: str) -> str | None:
        return f"gho_{handle}" if handle in connected else None

    async def _fake_profile(_session, handle: str):
        found = connected.get(handle)
        if found is None:
            return None
        user_id, login = found
        return (user_id, {"login": login, "name": login.capitalize()})

    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _fake_tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle", _fake_user_token
    )
    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_profile_for_handle", _fake_profile
    )
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(
        ws, "push_topic_branch", lambda pid, tid, token: f"topic/{tid.hex[:8]}"
    )
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo, **_: "main")


def _driving(monkeypatch, handle: str | None):
    """Make the endpoint believe `handle`'s turn is the one running.

    Production reads this off the runner's live lifecycle record; starting a real
    background turn just to read one handle back out would be testing the turn
    machinery instead of the ownership rule. What that record contains, and which
    handles it refuses to report, is covered in
    tests/unit/test_work_continuation.py."""
    runner = get_work_runner()
    monkeypatch.setattr(runner, "turn_author_for", lambda _topic_id: handle)


def _project(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _agent() -> str:
    return f"cheese-{uuid.uuid4().hex[:12]}"


def _add_project_member(client, project_id: str, handle: str) -> None:
    """Splitting is a project-level permission, so a caller who is not on the
    parent topic's roster still needs to be in the project."""
    from app.domain.identity.actor import Actor
    from app.domain.membership.services import MemberService
    from app.domain.project.models import ProjectRole

    async def _add() -> None:
        async with client.test_factory() as s:
            await MemberService(s).add(
                project_id=uuid.UUID(project_id),
                user_handle=handle,
                role=ProjectRole.member,
                actor=Actor(handle="alice", user_id=None, is_agent=False, via="token"),
            )
            await s.commit()

    asyncio.run(_add())


def _split(client, parent_id: str, *, by: str) -> dict:
    """Dispatch work the way `cheese split` does from a 分身's sandbox: no human
    token, the acting handle only in the body."""
    r = client.post(
        f"/topics/{parent_id}/split",
        json=dict(
            reviewer_handle="alice", **{"title": "分身拆出的子任务", "created_by": by}
        ),
    )
    assert r.status_code == 200
    return r.json()["data"]


def _roster(client, topic_id: str) -> dict[str, str]:
    members = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    return {m["member_handle"]: m["role"] for m in members}


def _pr_body(client, pid: str, tid: str) -> str:
    """房间递卡开出的 PR，正文长什么样。

    卡从**房间**递，因为递卡=封树开 PR，交付的是这条分支上一整批活（`cheese split`
    派出去的那些全在上面），支线自己递不了。
    """
    from app.domain.review import pr_publish

    card = client.post(
        f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            "change_subject": "fix(split): follow the driver, not the room",
        },
    )
    assert card.status_code == 200, card.text
    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(card.json()["data"]["id"]),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )
    [opened] = _FakeClient.opened
    return opened["body"]


def test_the_child_belongs_to_whoever_drove_the_turn(client, monkeypatch):
    """验收 1: alice's room, bob picks it up, 芝士 splits out of bob's turn. The
    child is bob's — and alice stays on the roster, so nothing is taken from her
    by making him owner."""
    _driving(monkeypatch, "bob")
    _, root = _project(client, owner="alice")

    assert _split(client, root, by=_agent())["owner_handle"] == "bob"
    # And nothing is taken from alice by making the work bob's: the ROOM is
    # still hers. That is the point of work having an owner instead of a roster
    # — one answer to "whose is this" that does not disturb another.
    assert _roster(client, root).get("alice") == "owner"


def test_an_autonomous_split_still_inherits_the_parent_owner(client, monkeypatch):
    """验收 2: a 分身 splitting on its own initiative identifies no driver, and
    the platform's own turns (gate verdicts, scheduled wake-ups) identify none
    either. Both must behave exactly as before — the parent's owner inherits."""
    _driving(monkeypatch, None)
    _, root = _project(client, owner="alice")

    assert _split(client, root, by=_agent())["owner_handle"] == "alice"


def test_a_human_who_splits_it_themselves_still_wins(client, monkeypatch):
    """The driver rung sits BELOW an explicit human splitter: carol calling split
    with her own credential said what she wanted, whoever else is talking."""
    _driving(monkeypatch, "bob")
    pid, root = _project(client, owner="alice")
    _add_project_member(client, pid, "carol")

    r = client.post(
        f"/topics/{root}/split",
        json=dict(reviewer_handle="alice", **{"title": "我自己拆的"}),
        headers={"Authorization": f"Bearer {session_token('carol')}"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["owner_handle"] == "carol"


def test_the_agent_handle_never_reaches_the_pr_however_the_work_was_split(
    client, monkeypatch
):
    """验收 4: 分身拆出去的活，交付时 PR 上署的必须是个真人。

    递卡是房间的事，所以署的是房间归属的那个人。**这条活由谁推进，PR 上看不出来**
    —— 一棵树 = 一个分支 = 一个 PR = 一批活，一个 PR 交付的是整批，没有"这个 PR 是
    bob 那件活的"这回事。这是「只有房间能递卡」换来的代价，不是遗漏：
    `identity.requester_handle` 的 `task_id` 分支（以及 `coauthor_handles` 的）从此
    没有调用方了，该怎么给一整棵树署名要另行决定。

    这里守住的是 PR #500 / #504 那条底线，它不受影响：分身自己的 `cheese-<hex12>`
    handle 一个字都不许出现在 PR 上。
    """
    _github_world(
        monkeypatch, connected={"alice": ("583231", "alice"), "bob": ("42", "bob")}
    )
    _driving(monkeypatch, "bob")
    pid, root = _project(client, owner="alice")
    agent = _agent()
    _split(client, root, by=agent)

    body = _pr_body(client, pid, root)
    assert "Requested-by: Alice <583231+alice@users.noreply.github.com>" in body
    assert agent not in body


def test_nobody_is_credited_twice_when_the_room_never_changed_hands(
    client, monkeypatch
):
    """验收 3: alice owns the room, so she is the commit's author already.
    `Co-authored-by: alice` next to `Requested-by: alice` claimed a second
    contributor that does not exist — this is the ordinary case, and it is why
    most changes carry no such trailer at all."""
    _github_world(monkeypatch, connected={"alice": ("583231", "alice")})
    _driving(monkeypatch, None)
    pid, root = _project(client, owner="alice")
    _split(client, root, by=_agent())

    body = _pr_body(client, pid, root)
    assert "Requested-by: Alice <583231+alice@users.noreply.github.com>" in body
    assert "Co-authored-by" not in body


def test_a_card_cannot_open_the_pr_for_the_batch_it_is_one_of(client, monkeypatch):
    """一件活自己递不出卡——这正是上面两条为什么都从房间递。

    放它过去的话，它会把兄弟们还在写的那条分支封口开 PR。走不通的方式是 404：
    卡不是地点。
    """
    _github_world(monkeypatch, connected={"alice": ("583231", "alice")})
    _driving(monkeypatch, "bob")
    _, root = _project(client, owner="alice")
    thread = _split(client, root, by=_agent())["id"]

    r = client.post(
        f"/topics/{thread}/tasks/{delivery_task_id(client, thread)}/accept-card",
        headers=delivery_headers(client, thread),
        json={
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            "change_subject": "fix(split): follow the driver, not the room",
        },
    )
    assert r.status_code == 404, r.text
