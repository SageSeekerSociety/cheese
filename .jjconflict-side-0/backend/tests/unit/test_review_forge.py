"""Which forge an accept goes through — the decision, on its own.

This crossing used to happen inline in `AcceptService.accept`, over a session, a
GitHub App and a workspace, which is why it was never unit-tested and why #362
happened: a GitHub-bound project's accept fell into the local merge and pushed
straight to main, twice in one day. Here it is a function with one injected
predicate, so every lane and the failure mode are three lines each.
"""

import uuid

import pytest

from app.core.errors import ValidationError
from app.domain.review import forge as forge_mod


def _pid() -> uuid.UUID:
    return uuid.uuid4()


async def _bound(_: uuid.UUID) -> bool:
    return True


async def _unbound(_: uuid.UUID) -> bool:
    return False


async def _explodes(_: uuid.UUID) -> bool:
    raise RuntimeError("upstream lookup died")


@pytest.mark.anyio
async def test_app_on_and_project_bound_is_the_app_forge() -> None:
    got = await forge_mod.resolve(
        project_id=_pid(), app_owns_prs=True, is_github_bound=_bound
    )

    assert got.kind is forge_mod.ForgeKind.github_app
    # The #362 rule: a PR is the only way in, and a PR-path failure stops the
    # accept instead of becoming a direct push.
    assert got.requires_pr is True
    assert got.has_external_checks is True
    assert got.note == ""


@pytest.mark.anyio
async def test_app_on_but_project_unbound_is_the_platform_forge() -> None:
    got = await forge_mod.resolve(
        project_id=_pid(), app_owns_prs=True, is_github_bound=_unbound
    )

    assert got.kind is forge_mod.ForgeKind.platform
    # The local merge is this project's accept, not a fallback from a failed one.
    assert got.requires_pr is False
    # And it is a repo with no CI configured — which #363 calls legitimate, so
    # the delivery surface must be able to say "no checks here" rather than
    # showing a check step that never runs.
    assert got.has_external_checks is False
    assert "未接 GitHub" in got.note


@pytest.mark.anyio
async def test_app_off_is_the_personal_token_forge_whatever_the_binding() -> None:
    """The pre-#296 world is kept deliberately, and the binding is not consulted.

    `_bound` here would raise the lane to github_app if it were asked; the point
    is that with the App mechanism off it must not be.
    """
    got = await forge_mod.resolve(
        project_id=_pid(), app_owns_prs=False, is_github_bound=_bound
    )

    assert got.kind is forge_mod.ForgeKind.github_user
    # This lane may degrade to a local merge — that is its documented behaviour.
    assert got.requires_pr is False
    assert got.note == ""


@pytest.mark.anyio
async def test_an_undeterminable_binding_stops_the_accept() -> None:
    """Fails closed, because the wrong guess is the one that pushes to main.

    Answering "unbound" on a database hiccup would send a GitHub-bound project
    down the local merge — exactly #362, arrived at by a different route.
    """
    with pytest.raises(ValidationError) as caught:
        await forge_mod.resolve(
            project_id=_pid(), app_owns_prs=True, is_github_bound=_explodes
        )

    assert "无法判定" in str(caught.value)


def test_every_kind_answers_every_question() -> None:
    """A new forge cannot be added half-way.

    Adding a `ForgeKind` without deciding its PR rule or whether it has external
    checks is how a lane ends up defaulting into someone else's behaviour.
    """
    for kind in forge_mod.ForgeKind:
        got = forge_mod.Forge(kind)
        assert isinstance(got.requires_pr, bool)
        assert isinstance(got.has_external_checks, bool)
        assert isinstance(got.note, str)
