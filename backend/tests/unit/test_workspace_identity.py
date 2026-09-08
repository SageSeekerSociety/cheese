"""Commit authorship: who a topic's commits belong to on GitHub.

The bug these close: every platform commit was authored by
`芝士 <cheese@zhishi.local>`, an address no GitHub account owns, so the work
landed as a grey unlinked name — no avatar, no link, no contribution credit for
the person who asked for it.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.topic_membership.services import TopicMemberService
from app.domain.workspace import identity


def _ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


def test_noreply_address_carries_the_numeric_id():
    """GitHub matches the account on the NUMBER; the login alone links nobody."""
    assert (
        identity.noreply_email("583231", "octocat")
        == "583231+octocat@users.noreply.github.com"
    )


def test_identity_comes_from_a_connected_github_profile():
    found = identity.identity_from_profile(
        "583231", {"login": "octocat", "name": "The Octocat"}
    )
    assert found == identity.GitIdentity(
        "The Octocat", "583231+octocat@users.noreply.github.com"
    )


def test_login_is_the_name_when_the_profile_has_none():
    found = identity.identity_from_profile("583231", {"login": "octocat"})
    assert found is not None
    assert found.name == "octocat"


def test_no_identity_rather_than_an_address_that_links_to_nobody():
    """A non-numeric id would build a perfectly plausible address that resolves
    to no account — worse than admitting there is no identity, because the
    commit then looks attributed and isn't."""
    assert identity.identity_from_profile("octocat", {"login": "octocat"}) is None
    assert identity.identity_from_profile("583231", {}) is None
    assert identity.identity_from_profile(None, {"login": "octocat"}) is None


def test_coauthor_trailer_is_omitted_for_the_platform_itself():
    """`Co-authored-by: 芝士 <cheese@zhishi.local>` would put an unlinkable
    address in permanent history for no gain — the trailer exists to credit a
    HUMAN."""
    assert identity.coauthored_by(None) is None
    assert identity.coauthored_by(identity.CHEESE_IDENTITY) is None
    assert identity.coauthored_by(
        identity.GitIdentity("octocat", "583231+octocat@users.noreply.github.com")
    ) == ("Co-authored-by: octocat <583231+octocat@users.noreply.github.com>")


def _topic(
    created_by: str | None, parent_id: uuid.UUID | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        created_by=created_by,
        parent_id=parent_id,
    )


def _roster_owner(monkeypatch, answer):
    """Stand in for the room's roster. `answer` is a handle, None, an exception to
    raise, or a dict mapping a topic id to any of those (for the parent/child pair
    the co-author rule reads)."""

    async def _owner_of(_self, topic_id):
        found = answer.get(topic_id) if isinstance(answer, dict) else answer
        if isinstance(found, BaseException):
            raise found
        return found

    monkeypatch.setattr(TopicMemberService, "owner_of", _owner_of)


def _connected(monkeypatch, accounts: dict[str, tuple[str, str]]):
    """Which handles have linked a GitHub account, as (numeric id, login)."""

    async def _profile(_session, handle: str):
        found = accounts.get(handle)
        return None if found is None else (found[0], {"login": found[1]})

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_profile_for_handle", _profile
    )


@pytest.mark.anyio
async def test_requester_is_the_rooms_human_owner_not_the_agent_that_split_it(
    monkeypatch,
):
    """The bug (PR #500, #504): a 分身 splits its sub-topics under its own
    `cheese-<hex12>` handle, so `created_by` named a robot with no GitHub
    account — the PR opened as the bot and the commits credited nobody. The
    roster already recorded the real human; this reads it."""
    _roster_owner(monkeypatch, "alice")
    who = await identity.requester_handle(None, _topic("cheese-a7a0268b96ff"))
    assert who == "alice"


@pytest.mark.anyio
async def test_a_room_a_human_opened_is_unchanged(monkeypatch):
    """Owner and creator are the same person there — the answer must not move."""
    _roster_owner(monkeypatch, "alice")
    assert await identity.requester_handle(None, _topic("alice")) == "alice"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "roster",
    [
        pytest.param("cheese", id="owner_is_the_platform_agent"),
        pytest.param("cheese-a7a0268b96ff", id="owner_is_a_topic_agent"),
        pytest.param(None, id="room_has_no_owner"),
        pytest.param(RuntimeError("roster unreadable"), id="roster_read_blew_up"),
    ],
)
async def test_no_human_on_the_roster_falls_back_to_created_by(monkeypatch, roster):
    """Every failure mode lands on the PREVIOUS behaviour, never worse than it
    and never an exception: attribution must not be why a PR fails to open."""
    _roster_owner(monkeypatch, roster)
    assert await identity.requester_handle(None, _topic("bob")) == "bob"


@pytest.mark.anyio
async def test_nobody_at_all_resolves_to_nobody(monkeypatch):
    """No owner and no creator — callers must get None, not an empty handle
    they would go on to look up."""
    _roster_owner(monkeypatch, None)
    assert await identity.requester_handle(None, _topic(None)) is None
    assert await identity.requester_handle(None, _topic("")) is None


# --- Co-authors: who is credited on a change besides the person it belongs to ---
#
# 归属跟推进者走 (拍板 2026-08-17): when a room changes hands, the sub-topics split
# out of the new driver's turns are the driver's — that is what keeps the accept
# card landing on someone still working on it — and the person who asked for the
# thing in the first place is credited with `Co-authored-by:` instead of vanishing.


@pytest.mark.anyio
async def test_the_parent_rooms_owner_is_credited_when_the_child_is_someone_elses(
    monkeypatch,
):
    parent = uuid.uuid4()
    child = _topic("cheese-a7a0268b96ff", parent_id=parent)
    _roster_owner(monkeypatch, {child.id: "bob", parent: "alice"})
    _connected(monkeypatch, {"alice": ("583231", "alice"), "bob": ("42", "bob")})

    who = await identity.attribution(None, child)
    assert who.handle == "bob"
    assert who.requester == identity.GitIdentity(
        "bob", "42+bob@users.noreply.github.com"
    )
    assert who.author == identity.agent_identity(identity.topic_agent_handle(child.id))
    assert who.coauthors == ()


@pytest.mark.anyio
async def test_a_room_that_never_changed_hands_credits_nobody_twice(monkeypatch):
    """The redundancy this removed: one room has one git identity, so naming the
    commit's own author as a co-author claimed a contributor who does not exist."""
    parent = uuid.uuid4()
    child = _topic("cheese-a7a0268b96ff", parent_id=parent)
    _roster_owner(monkeypatch, {child.id: "alice", parent: "alice"})
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    assert (await identity.attribution(None, child)).coauthors == ()


@pytest.mark.anyio
async def test_two_handles_on_one_github_account_are_not_credited_twice(monkeypatch):
    """The handles differ, so the handle-level check passes — but they resolve to
    the same account, and a trailer naming the commit's own author is exactly the
    noise this removed."""
    parent = uuid.uuid4()
    child = _topic(None, parent_id=parent)
    _roster_owner(monkeypatch, {child.id: "bob", parent: "bob-old"})
    _connected(monkeypatch, {"bob": ("42", "bob"), "bob-old": ("42", "bob")})

    assert (await identity.attribution(None, child)).coauthors == ()


@pytest.mark.anyio
async def test_a_top_level_room_has_no_coauthors_at_all(monkeypatch):
    """The ordinary case, and the reason most changes now carry no such trailer:
    nobody handed this work over, so there is no second contributor to name."""
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    assert (await identity.attribution(None, _topic("alice"))).coauthors == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "parent_owner",
    [
        pytest.param("cheese", id="parent_is_owned_by_the_platform_agent"),
        pytest.param("cheese-c43d2e126d4f", id="parent_is_owned_by_a_topic_agent"),
        pytest.param(None, id="parent_has_no_owner"),
        pytest.param(RuntimeError("roster unreadable"), id="parent_roster_blew_up"),
    ],
)
async def test_only_a_real_person_is_ever_credited(monkeypatch, parent_owner):
    """芝士 is never a co-author (拍板 2026-08-17): every commit here is one she
    typed, so the trailer would be true of every change and carry no information.
    A broken roster read degrades the same way — silently, never by raising."""
    parent = uuid.uuid4()
    child = _topic("cheese-a7a0268b96ff", parent_id=parent)
    _roster_owner(monkeypatch, {child.id: "bob", parent: parent_owner})
    _connected(monkeypatch, {"bob": ("42", "bob")})

    who = await identity.attribution(None, child)
    assert who.handle == "bob"
    assert who.coauthors == ()


@pytest.mark.anyio
async def test_a_coauthor_without_a_github_account_is_simply_not_credited(monkeypatch):
    """`Co-authored-by:` only works with an address GitHub can link. Inventing one
    would produce a trailer that looks like credit and links to nobody."""
    parent = uuid.uuid4()
    child = _topic(None, parent_id=parent)
    _roster_owner(monkeypatch, {child.id: "bob", parent: "alice"})
    _connected(monkeypatch, {"bob": ("42", "bob")})

    who = await identity.attribution(None, child)
    assert who.handle == "bob"
    assert who.coauthors == ()


# --- The machines: which 分身 did which piece of work in this delivery (#189) ---
#
# A commit's human trailers cannot answer that, and neither can `Co-authored-by:
# Claude Fable 5`, which every Claude Code commit anywhere carries. The answer is
# the one the card was FILED with: the room says whose code this is, because the
# room is the only party that knows, and nothing here guesses when it doesn't.


def _task(subagent_id: str | None, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        subagent_id=subagent_id,
        title=title,
        owner_handle=None,
        created_by=None,
    )


def _card(declares, task_id: uuid.UUID | None = None) -> SimpleNamespace:
    """An accept card as it reaches attribution — `declares` is what the room
    said this delivery carries, in the column's own form (JSON strings)."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        task_id=task_id,
        delivered_task_ids=list(declares),
    )


def _rows(monkeypatch, tasks):
    """Stand in for the task table. `tasks` is every row that exists, or an
    exception the read raises."""
    from app.domain.room_task.services import TaskService

    async def _list_by_ids(_self, task_ids):
        if isinstance(tasks, BaseException):
            raise tasks
        wanted = set(task_ids)
        return [t for t in tasks if t.id in wanted]

    monkeypatch.setattr(TaskService, "list_by_ids", _list_by_ids)


@pytest.mark.anyio
async def test_the_delivery_names_the_work_the_card_declared(monkeypatch):
    mine = _task("ac2c038d44616a2f2", "补 trailer")
    theirs = _task("9f1b7c22e0d341a80", "修 flaky")
    _rows(monkeypatch, [mine, theirs])
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(
        None, _topic("alice"), card=_card([str(mine.id), str(theirs.id)])
    )
    assert who.tasks == (
        identity.WorkItem(mine.id, "ac2c038d44616a2f2", "补 trailer"),
        identity.WorkItem(theirs.id, "9f1b7c22e0d341a80", "修 flaky"),
    )


@pytest.mark.anyio
async def test_work_the_card_did_not_declare_is_not_named(monkeypatch):
    """The room has other threads — one that wrote nothing, one still running —
    and neither wrote this change. Only what was declared may be signed on."""
    mine = _task("ac2c038d44616a2f2", "补 trailer")
    placeholder = _task(None, "占位")
    running = _task("9f1b7c22e0d341a80", "还在跑")
    _rows(monkeypatch, [mine, placeholder, running])
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(None, _topic("alice"), card=_card([str(mine.id)]))
    assert [item.task_id for item in who.tasks] == [mine.id]


@pytest.mark.anyio
async def test_a_card_that_declared_nothing_names_nobody(monkeypatch):
    """诚实的空白, and no fallback. Work exists in this room and some of it may
    even be in this change — but nothing here can tell which, and a plausible
    wrong name in permanent history is worse than no name, because an audit
    believes it."""
    _rows(monkeypatch, [_task("ac2c038d44616a2f2", "补 trailer")])
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    empty = await identity.attribution(None, _topic("alice"), card=_card([]))
    assert empty.tasks == ()
    assert (await identity.attribution(None, _topic("alice"))).tasks == ()


@pytest.mark.anyio
async def test_work_nobody_was_bound_to_keeps_its_place_in_the_batch(monkeypatch):
    """`subagent_id` is NULL until a worker is bound, and a room can write a
    change itself. Dropping the row would make the batch in the commit smaller
    than the batch the room declared."""
    mine = _task(None, "人自己动手改的")
    _rows(monkeypatch, [mine])
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(None, _topic("alice"), card=_card([str(mine.id)]))
    assert who.tasks == (identity.WorkItem(mine.id, None, "人自己动手改的"),)


@pytest.mark.anyio
async def test_a_declared_id_whose_row_is_gone_costs_only_its_own_line(monkeypatch):
    """The declaration is a list of ids, not a foreign key, so it can outlive
    what it names. The rest of the batch still gets its credit."""
    mine = _task("ac2c038d44616a2f2", "补 trailer")
    _rows(monkeypatch, [mine])
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(
        None,
        _topic("alice"),
        card=_card([str(mine.id), str(uuid.uuid4()), "not-a-uuid"]),
    )
    assert [item.task_id for item in who.tasks] == [mine.id]


@pytest.mark.anyio
async def test_unreadable_work_costs_the_trailers_and_nothing_else(monkeypatch):
    """Same rule as every other part of attribution: a trailer is not worth
    failing a merge, and one broken lookup must not take the rest with it — the
    person who asked still gets their credit."""
    _rows(monkeypatch, RuntimeError("tasks unreadable"))
    _roster_owner(monkeypatch, "alice")
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(
        None, _topic("alice"), card=_card([str(uuid.uuid4())])
    )
    assert who.tasks == ()
    assert who.handle == "alice"
    assert who.requester == identity.GitIdentity(
        "alice", "583231+alice@users.noreply.github.com"
    )


def test_session_sidecars_share_one_base_directory(tmp_path, monkeypatch):
    """The identity file sits beside the hook spool; two definitions of "this
    topic's session dir" is how they drift apart."""
    from app.domain.workspace import service as ws

    monkeypatch.setattr(identity.settings, "workspace_root", str(tmp_path))
    pid, tid = _ids()
    base = identity.session_dir(pid, tid)
    assert ws.spool_dir(pid, tid).parent == base


@pytest.mark.anyio
async def test_declared_reporter_and_code_contributor_have_distinct_git_trailers(
    monkeypatch,
):
    import subprocess

    from app.domain.review.pr_text import pr_trailers

    task = _task("worker-1", "fix bug")
    task.reporter_handle = "reporter"
    task.contributor_handles = ["coder"]
    _rows(monkeypatch, [task])
    _roster_owner(monkeypatch, "requester")
    _connected(monkeypatch, {"coder": ("42", "coder")})
    topic = _topic("requester")
    who = await identity.attribution(
        None, topic, card=_card([str(task.id)]), decided_by="reviewer"
    )
    body = pr_trailers(topic, "reviewer", who)
    parsed = subprocess.check_output(
        ["git", "interpret-trailers", "--parse"], input="Fix bug\n\n" + body, text=True
    )
    assert "Requested-by: requester <requester@zhishi.local>" in parsed
    assert "Reported-by: reporter <reporter@zhishi.local>" in parsed
    assert "Reviewed-by: reviewer <reviewer@zhishi.local>" in parsed
    assert "Co-authored-by: coder <42+coder@users.noreply.github.com>" in parsed
    assert "Co-authored-by: requester" not in parsed
    assert who.author == identity.agent_identity(identity.topic_agent_handle(topic.id))
