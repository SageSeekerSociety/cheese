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


def _topic(created_by: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), created_by=created_by
    )


def _roster_owner(monkeypatch, answer):
    """Stand in for the room's roster. `answer` is a handle, None, or an
    exception to raise."""

    async def _owner_of(_self, _topic_id):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(TopicMemberService, "owner_of", _owner_of)


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
