"""Which forge an accept goes through — the decision, on its own.

This crossing used to happen inline in `AcceptService.accept`, over a session, a
GitHub App and a workspace, which is why it was never unit-tested and why #362
happened: a GitHub-bound project's accept fell into the local merge and pushed
straight to main, twice in one day. Here it is a function with one injected
predicate, so every lane and the failure mode are three lines each.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import UnprocessableEntityError, ValidationError
from app.domain.review import forge as forge_mod


def _pid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    from app.domain.project import forge

    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=object()))


def _session(kind="github_app", host="github.com"):
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        kind=kind, url=f"https://{host}/acme/widgets.git"
    )
    return session


@pytest.mark.anyio
async def test_a_bound_project_is_the_app_forge() -> None:
    got = await forge_mod.resolve(project_id=_pid(), session=_session())

    assert got.kind is forge_mod.ForgeKind.github_app
    assert got.capabilities.reports_checks is True
    assert got.declaration == ""


@pytest.mark.anyio
async def test_forgejo_binding_uses_proposals_and_external_checks() -> None:
    got = await forge_mod.resolve(
        project_id=_pid(), session=_session("forgejo", "forge.invalid")
    )
    assert got.kind is forge_mod.ForgeKind.forgejo
    assert got.capabilities.reports_checks is True


@pytest.mark.anyio
async def test_unbound_project_cannot_merge_locally() -> None:
    session = _session()
    session.scalar.return_value = None
    with pytest.raises(ValidationError, match="没有代码仓库"):
        await forge_mod.resolve(project_id=_pid(), session=session)


@pytest.mark.anyio
async def test_missing_credentials_do_not_change_the_provider(monkeypatch):
    from app.domain.project import forge

    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=None))
    got = await forge_mod.resolve(project_id=_pid(), session=_session())
    assert got.kind is forge_mod.ForgeKind.github_app
    assert got.capabilities.hosts_proposals is True
    assert got.capabilities.can_write_remote is False
    assert got.capabilities.pushes_to_external_remote is False


@pytest.mark.anyio
async def test_an_undeterminable_binding_stops_the_accept() -> None:
    """Fails closed, because the wrong guess is the one that pushes to main.

    Answering "unbound" on a database hiccup would send a GitHub-bound project
    down the local merge — exactly #362, arrived at by a different route.
    """
    session = _session()
    session.scalar.side_effect = RuntimeError("database unavailable")
    with pytest.raises(ValidationError, match="无法读取"):
        await forge_mod.resolve(project_id=_pid(), session=session)


@pytest.mark.anyio
async def test_every_kind_answers_every_question() -> None:
    """A new forge cannot be added half-way.

    Every registered provider declares checks and implements the operations.
    """
    for kind in forge_mod.ForgeKind:
        got = await forge_mod.resolve(project_id=_pid(), session=_session(kind))
        assert isinstance(got.capabilities.reports_checks, bool)
        assert isinstance(got.declaration, str)


@pytest.mark.anyio
async def test_proposal_cannot_cross_to_a_different_forge():
    with pytest.raises(ValidationError, match="不一致"):
        await forge_mod.resolve(
            project_id=_pid(),
            session=_session("forgejo", "forge.invalid"),
            proposal_url="https://github.com/acme/widgets/pull/42",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["file", "link"])
async def test_pr_less_file_or_link_card_renders_clean_and_accepts_without_pr(
    monkeypatch, kind
) -> None:
    """Standalone file/link cards with no branch commits do not open or merge a PR."""
    from datetime import UTC, datetime

    from app.domain.review import pr_publish
    from app.domain.review.models import AcceptStatus, DeliverableKind
    from app.domain.review.services import AcceptService, cards

    deliverable_kind = DeliverableKind.file if kind == "file" else DeliverableKind.link
    project_id = _pid()
    topic_id = uuid.uuid4()
    service = object.__new__(AcceptService)
    service._session = _session()
    # `_render` 折审阅人那句话要问仓库（一卡多人，`reviewer_handles` 是集合）。
    service._repo = SimpleNamespace(reviewer_handles=AsyncMock(return_value=("alice",)))
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=topic_id,
        task_id=uuid.uuid4(),
        delivered_task_ids=[],
        reviewer_handle="alice",
        focus="审阅文件",
        change_subject="docs: deliver artifact",
        change_body=None,
        status=AcceptStatus.pending,
        decided_by=None,
        decided_at=None,
        note="",
        note_code=None,
        note_level=None,
        created_at=datetime.now(UTC),
        gate_passed_at=None,
        gate_output="",
        pr_number=None,
        pr_url=None,
        pr_head_sha=None,
        merge_state=None,
        auto_merge_armed_by=None,
        auto_merge_armed_at=None,
        artifact_id=None,
        deliverable_kind=deliverable_kind,
        deliverable_name="报告.pdf" if kind == "file" else None,
        deliverable_url="https://example.com/demo" if kind == "link" else None,
        completes_task=True,
    )
    forge = await forge_mod.resolve(project_id=project_id, session=_session())
    rendered = await cards._render(
        service,
        card,
        approvals={},
        trees={},
        rooms={topic_id: SimpleNamespace(id=topic_id, project_id=project_id)},
        projects={project_id: None},
        forges={(project_id, ""): forge},
        hidden={},
        summaries={},
    )
    assert rendered["merge_state"]["state"] == "clean"
    assert rendered["merge_state"]["who"] == "human"
    assert rendered["merge_state"]["reasons"] == [
        {"kind": "no_obstacle", "checks": [], "detail": "可以合并"}
    ]
    assert rendered["merge_state"]["head_sha"] is None

    # _accept_github skips PR publication/merge and settles via real _accept_discussion
    published = AsyncMock()
    service._publish_pr_for_accept = published
    service._repo = SimpleNamespace(
        add_approval=AsyncMock(),
        reviewer_handles=AsyncMock(return_value=("alice",)),
    )
    service._settle_the_card = AsyncMock()
    service._stamp_delivery = AsyncMock()
    service._mark_task_merged = AsyncMock(return_value=True)
    service._session = SimpleNamespace(
        flush=AsyncMock(), refresh=AsyncMock(), get=AsyncMock(return_value=None)
    )
    # What an acceptance pays an invited newcomer is the invitation ledger's
    # rule (tests/integration/test_invite_referrals.py); here only that it is told.
    from app.domain.usage import referrals

    told = AsyncMock()
    monkeypatch.setattr(referrals, "on_accepted", told)
    topic = SimpleNamespace(id=card.topic_id, project_id=_pid())
    got = await service._accept_github(card, topic, "alice", seen_head=None)
    assert got is card
    told.assert_awaited_once_with(service._session, "alice")
    assert card.status == AcceptStatus.accepted
    assert card.decided_by == "alice"
    published.assert_not_awaited()

    # open_pr_for_card returns None when delivered_task_ids is empty
    monkeypatch.setattr(
        pr_publish,
        "proposal_client",
        AsyncMock(return_value=SimpleNamespace(open_pr=AsyncMock())),
    )
    monkeypatch.setattr(
        pr_publish,
        "AcceptCardRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=card)),
    )
    opened = await pr_publish.open_pr_for_card(
        _session(),
        card_id=card.id,
        topic_id=card.topic_id,
        project_id=_pid(),
    )
    assert opened is None

    # A file/link card that DOES carry branch commits (delivered_task_ids non-empty)
    # still requires and publishes a PR instead of accepting without merging.
    card_with_commits = SimpleNamespace(
        **{
            **card.__dict__,
            "status": AcceptStatus.pending,
            "decided_by": None,
            "decided_at": None,
            "delivered_task_ids": [str(card.task_id)],
        }
    )
    rendered_with_commits = await cards._render(
        service,
        card_with_commits,
        approvals={},
        trees={},
        rooms={topic_id: SimpleNamespace(id=topic_id, project_id=project_id)},
        projects={project_id: None},
        forges={(project_id, ""): forge},
        hidden={},
        summaries={},
    )
    assert rendered_with_commits["merge_state"]["state"] == "unknown"
    assert rendered_with_commits["merge_state"]["who"] == "platform"
    assert (
        rendered_with_commits["merge_state"]["reasons"][0]["detail"]
        == "PR 尚未创建，检查状态未知"
    )
    service._stop_accept_no_branch = AsyncMock(
        side_effect=UnprocessableEntityError("no branch")
    )
    with pytest.raises(UnprocessableEntityError, match="no branch"):
        await service._accept_github(card_with_commits, topic, "alice", seen_head=None)
    published.assert_awaited_once_with(card_with_commits, topic)
    assert card_with_commits.status == AcceptStatus.pending
