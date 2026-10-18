"""Who a delivery is credited to: the task owner, the agent, the declared people."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.repository import identity
from app.domain.topic_membership.services import TopicMemberService


@pytest.fixture(autouse=True)
def project(monkeypatch):
    """The project the topic belongs to; tests set its `settings`."""
    from app.domain.project.repositories import ProjectRepository

    project = SimpleNamespace(settings={})

    async def get(_self, _project_id):
        return project

    monkeypatch.setattr(ProjectRepository, "get", get)
    return project


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


def test_a_coauthor_is_one_trailer_line():
    assert identity.coauthored_by(None) is None
    assert identity.coauthored_by(
        identity.GitIdentity("octocat", "583231+octocat@users.noreply.github.com")
    ) == ("Co-authored-by: octocat <583231+octocat@users.noreply.github.com>")


def _topic(created_by: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        created_by=created_by,
        parent_id=None,
        title="Test room",
    )


# 房间里坐着的那个 agent，署名署的就是它自己的 handle——不是从房间派生的名字。
_SEATED_AGENT = "cheese-a7a0268b96ff"


@pytest.fixture(autouse=True)
def seated_agent(monkeypatch):
    async def _agent_of(_self, topic_id):
        return _SEATED_AGENT

    monkeypatch.setattr(TopicMemberService, "resolve_agent_handle", _agent_of)


def _connected(monkeypatch, accounts: dict[str, tuple[str, str]]):
    """Which handles have linked a GitHub account, as (numeric id, login)."""

    async def _profile(_session, handle: str):
        found = accounts.get(handle)
        return None if found is None else (found[0], {"login": found[1]})

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_profile_for_handle", _profile
    )


def _task(
    title: str = "做一件事",
    *,
    owner: str | None = None,
    created_by: str | None = None,
    contributors: tuple[str, ...] = (),
    reporter: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        owner_handle=owner,
        created_by=created_by,
        author_handle=_SEATED_AGENT,
        contributor_handles=list(contributors),
        reporter_handle=reporter,
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
    from app.domain.repository import task_rows

    async def _many(_session, task_ids):
        if isinstance(tasks, BaseException):
            raise tasks
        wanted = set(task_ids)
        return [t for t in tasks if t.id in wanted]

    async def _one(_session, task_id):
        if isinstance(tasks, BaseException):
            raise tasks
        return next((t for t in tasks if t.id == task_id), None)

    monkeypatch.setattr(task_rows, "many", _many)
    monkeypatch.setattr(task_rows, "one", _one)


ALICE = identity.GitIdentity("alice", "583231+alice@users.noreply.github.com")
APP = identity.app_identity(314883224)


# --- Whose change it is: the task owner ---


@pytest.mark.anyio
async def test_the_owner_is_the_human_who_owns_the_task(monkeypatch):
    task = _task(owner="alice", created_by="cheese-a7a0268b96ff")
    _rows(monkeypatch, [task])
    assert await identity.owner_handle(None, task.id) == "alice"


@pytest.mark.anyio
async def test_a_task_an_agent_owns_falls_back_to_the_human_who_created_it(
    monkeypatch,
):
    task = _task(owner="cheese-a7a0268b96ff", created_by="alice")
    _rows(monkeypatch, [task])
    assert await identity.owner_handle(None, task.id) == "alice"


@pytest.mark.anyio
async def test_a_task_no_human_is_behind_credits_nobody(monkeypatch):
    """An agent opened it and owns it. Nobody asked for this change, and
    crediting the channel's owner instead would name someone who may never have
    seen it."""
    task = _task(owner="cheese-a7a0268b96ff", created_by="cheese")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {})
    who = await identity.attribution(None, _topic("bob"), task_id=task.id)
    assert who.owner_handle is None
    assert who.coauthors == ()


# --- The two credit modes ---


@pytest.mark.anyio
async def test_by_default_an_app_opened_pr_credits_the_owner_as_coauthor(monkeypatch):
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice")})
    who = await identity.attribution(
        None, _topic(), task_id=task.id, app=APP, lane="github"
    )
    assert who.coauthors == (ALICE,)


@pytest.mark.anyio
async def test_a_pr_the_owner_opened_credits_the_app_beside_them(monkeypatch):
    """GitHub makes the PR's opener the squash commit's author: the owner is
    already on the commit, and the App is the co-author whose logo shows."""
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice")})
    who = await identity.attribution(
        None, _topic(), task_id=task.id, opened_by=ALICE.email, app=APP, lane="github"
    )
    assert who.coauthors == (APP,)


@pytest.mark.anyio
async def test_an_owner_without_github_is_credited_under_the_platforms_address(
    monkeypatch,
):
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {})
    who = await identity.attribution(None, _topic(), task_id=task.id)
    assert who.coauthors == (identity.platform_identity("alice"),)


@pytest.mark.anyio
async def test_the_agent_mode_names_no_owner(monkeypatch, project):
    project.settings = {identity.CREDIT_SETTING: identity.CREDIT_AGENT}
    task = _task(owner="alice", contributors=("coder",))
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice"), "coder": ("42", "coder")})
    who = await identity.attribution(
        None, _topic(), card=_card([str(task.id)], task.id), task_id=task.id
    )
    assert who.coauthors == (
        identity.GitIdentity("coder", "42+coder@users.noreply.github.com"),
    )


@pytest.mark.anyio
async def test_a_declared_contributor_is_credited_in_either_mode(monkeypatch, project):
    task = _task(owner="alice", contributors=("coder",), reporter="reporter")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"coder": ("42", "coder")})
    coder = identity.GitIdentity("coder", "42+coder@users.noreply.github.com")
    for mode in identity.CREDIT_MODES:
        project.settings = {identity.CREDIT_SETTING: mode}
        who = await identity.attribution(
            None, _topic(), card=_card([str(task.id)], task.id)
        )
        assert coder in who.coauthors
        assert who.reporters == (identity.platform_identity("reporter"),)


@pytest.mark.anyio
async def test_the_author_of_the_commit_is_never_also_its_coauthor(monkeypatch):
    """A contributor who opened the PR is already its author."""
    task = _task(owner="alice", contributors=("alice",))
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice")})
    who = await identity.attribution(
        None,
        _topic(),
        card=_card([str(task.id)], task.id),
        opened_by=ALICE.email,
        app=APP,
        lane="github",
    )
    assert who.coauthors == (APP,)


@pytest.mark.anyio
async def test_on_github_the_owner_authors_the_branch_commits_and_the_app_is_credited(
    monkeypatch,
):
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice")})
    who = await identity.attribution(
        None, _topic(), task_id=task.id, app=APP, lane="github"
    )
    assert who.commit_author == ALICE
    assert who.commit_coauthors == (APP,)


@pytest.mark.anyio
async def test_on_github_the_agent_mode_commits_as_the_app(monkeypatch, project):
    project.settings = {identity.CREDIT_SETTING: identity.CREDIT_AGENT}
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {"alice": ("583231", "alice")})
    who = await identity.attribution(
        None, _topic(), task_id=task.id, app=APP, lane="github"
    )
    assert who.commit_author == APP
    assert who.commit_coauthors == ()


@pytest.mark.anyio
async def test_on_the_platform_forge_芝士_authors_and_the_owner_is_credited(
    monkeypatch,
):
    """Each address there belongs to one account: the owner's cannot be the
    author in every project, so 芝士 is, under its own address, by name."""
    task = _task(owner="alice")
    _rows(monkeypatch, [task])
    _connected(monkeypatch, {})
    who = await identity.attribution(None, _topic(), task_id=task.id, lane="forgejo")
    assert who.commit_author == identity.GitIdentity(
        identity.CHEESE_NAME, identity.agent_identity(_SEATED_AGENT).email
    )
    assert who.commit_coauthors == (identity.platform_identity("alice"),)
    assert who.coauthors == who.commit_coauthors


def test_an_unknown_mode_reads_as_the_owner_mode():
    assert identity.credit_mode({}) == identity.CREDIT_OWNER
    assert identity.credit_mode({identity.CREDIT_SETTING: "x"}) == identity.CREDIT_OWNER


# --- Which pieces of work went into this delivery (#189) ---
#
# A commit's human trailers cannot answer that, and neither can `Co-authored-by:
# Claude Fable 5`, which every Claude Code commit anywhere carries. The answer is
# the one the card was FILED with: the room says whose code this is, because the
# room is the only party that knows, and nothing here guesses when it doesn't.


@pytest.mark.anyio
async def test_the_delivery_names_the_work_the_card_declared(monkeypatch):
    mine = _task("补 trailer")
    theirs = _task("修 flaky")
    _rows(monkeypatch, [mine, theirs])
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(
        None, _topic("alice"), card=_card([str(mine.id), str(theirs.id)])
    )
    assert who.tasks == (
        identity.WorkItem(mine.id, "补 trailer"),
        identity.WorkItem(theirs.id, "修 flaky"),
    )


@pytest.mark.anyio
async def test_work_the_card_did_not_declare_is_not_named(monkeypatch):
    """The room has other threads — one that wrote nothing, one still running —
    and neither wrote this change. Only what was declared may be signed on."""
    mine = _task("补 trailer")
    placeholder = _task("占位")
    running = _task("还在跑")
    _rows(monkeypatch, [mine, placeholder, running])
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(None, _topic("alice"), card=_card([str(mine.id)]))
    assert [item.task_id for item in who.tasks] == [mine.id]


@pytest.mark.anyio
async def test_a_card_that_declared_nothing_names_nobody(monkeypatch):
    """诚实的空白, and no fallback. Work exists in this room and some of it may
    even be in this change — but nothing here can tell which, and a plausible
    wrong name in permanent history is worse than no name, because an audit
    believes it."""
    _rows(monkeypatch, [_task("补 trailer")])
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    empty = await identity.attribution(None, _topic("alice"), card=_card([]))
    assert empty.tasks == ()
    assert (await identity.attribution(None, _topic("alice"))).tasks == ()


@pytest.mark.anyio
async def test_a_declared_id_whose_row_is_gone_costs_only_its_own_line(monkeypatch):
    """The declaration is a list of ids, not a foreign key, so it can outlive
    what it names. The rest of the batch still gets its credit."""
    mine = _task("补 trailer")
    _rows(monkeypatch, [mine])
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
    rest of the attribution still resolves."""
    _rows(monkeypatch, RuntimeError("tasks unreadable"))
    _connected(monkeypatch, {"alice": ("583231", "alice")})

    who = await identity.attribution(
        None, _topic("alice"), card=_card([str(uuid.uuid4())])
    )
    assert who.tasks == ()
    assert who.author == identity.agent_identity(_SEATED_AGENT)
