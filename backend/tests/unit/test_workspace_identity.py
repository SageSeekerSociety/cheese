"""Commit authorship: who a topic's commits belong to on GitHub.

The bug these close: every platform commit was authored by
`芝士 <cheese@zhishi.local>`, an address no GitHub account owns, so the work
landed as a grey unlinked name — no avatar, no link, no contribution credit for
the person who asked for it.
"""

import json
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


def test_remembered_identity_survives_to_the_snapshot_path(tmp_path, monkeypatch):
    """`snapshot_worktree` runs in a worker thread with no DB session, so the
    identity has to be on disk by the time it commits."""
    monkeypatch.setattr(identity.settings, "workspace_root", str(tmp_path))
    pid, tid = _ids()
    assert identity.read(pid, tid) is None

    who = identity.GitIdentity("octocat", "583231+octocat@users.noreply.github.com")
    identity.remember(pid, tid, who)
    assert identity.read(pid, tid) == who


def test_a_corrupt_sidecar_falls_back_instead_of_raising(tmp_path, monkeypatch):
    """Authorship is a nicety; it must never be why a turn's work fails to
    commit."""
    monkeypatch.setattr(identity.settings, "workspace_root", str(tmp_path))
    pid, tid = _ids()
    path = identity.identity_path(pid, tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert identity.read(pid, tid) is None


def test_rewriting_is_skipped_when_nothing_changed(tmp_path, monkeypatch):
    """Called every turn, so an unchanged identity must not churn the file."""
    monkeypatch.setattr(identity.settings, "workspace_root", str(tmp_path))
    pid, tid = _ids()
    who = identity.GitIdentity("octocat", "583231+octocat@users.noreply.github.com")
    identity.remember(pid, tid, who)
    path = identity.identity_path(pid, tid)
    before = path.stat().st_mtime_ns
    identity.remember(pid, tid, who)
    assert path.stat().st_mtime_ns == before
    assert json.loads(path.read_text())["email"] == who.email


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
    assert who.author == identity.GitIdentity("bob", "42+bob@users.noreply.github.com")
    assert who.coauthors == (
        identity.GitIdentity("alice", "583231+alice@users.noreply.github.com"),
    )


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


def test_session_sidecars_share_one_base_directory(tmp_path, monkeypatch):
    """The identity file sits beside the await logs and the hook spool; three
    definitions of "this topic's session dir" is how they drift apart."""
    from app.domain.workspace import service as ws

    monkeypatch.setattr(identity.settings, "workspace_root", str(tmp_path))
    pid, tid = _ids()
    base = identity.session_dir(pid, tid)
    assert ws.spool_dir(pid, tid).parent == base
    assert ws.await_log_dir(pid, tid).parent == base
    assert identity.identity_path(pid, tid).parent == base
