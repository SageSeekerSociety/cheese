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

from app.core.errors import ValidationError
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
