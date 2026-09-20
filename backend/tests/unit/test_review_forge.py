"""Which forge an accept goes through — the decision, on its own.

This crossing used to happen inline in `AcceptService.accept`, over a session, a
GitHub App and a workspace, which is why it was never unit-tested and why #362
happened: a GitHub-bound project's accept fell into the local merge and pushed
straight to main, twice in one day.

It then spent a while as one injected boolean, `is_github_bound`, which is a
different way to be wrong: a project with a remote we CAN write but that is not
github.com answered False and got the platform forge, so every accept landed
only in our copy of the repository and the user's remote received nothing.

Now it is capabilities, computed from three facts, and this file is the
decision: what the bits say, which implementation serves them, and what happens
when the facts cannot be read.
"""

import uuid

import pytest

from app.core.errors import ValidationError
from app.domain.review import forge as forge_mod


def _pid() -> uuid.UUID:
    return uuid.uuid4()


def _facts(**over) -> forge_mod.ProjectForgeFacts:
    base = {
        "github_app_installed": False,
        "has_external_remote": False,
        "remote_write_credential": False,
    }
    return forge_mod.ProjectForgeFacts(**{**base, **over})


def _answers(facts: forge_mod.ProjectForgeFacts):
    async def _read(_: uuid.UUID) -> forge_mod.ProjectForgeFacts:
        return facts

    return _read


async def _explodes(_: uuid.UUID) -> forge_mod.ProjectForgeFacts:
    raise RuntimeError("upstream lookup died")


# ---- 能力位是算出来的，不是存下来的 ----------------------------------------


def test_the_app_on_a_remote_reports_checks_and_hosts_proposals() -> None:
    caps = forge_mod.capabilities_of(
        _facts(
            github_app_installed=True,
            has_external_remote=True,
            remote_write_credential=True,
        )
    )

    assert caps.reports_checks is True
    assert caps.hosts_proposals is True
    assert caps.pushes_to_external_remote is True
    assert caps.identity is forge_mod.ForgeIdentity.user


def test_a_remote_we_cannot_write_does_not_push() -> None:
    """有地址和推得动是两件事，合取才成立。

    A project that filled in an address we hold no credential for used to get a
    forge that could never push — the mirror image of the same hole.
    """
    caps = forge_mod.capabilities_of(
        _facts(has_external_remote=True, remote_write_credential=False)
    )

    assert caps.can_write_remote is False
    assert caps.pushes_to_external_remote is False


def test_a_credential_with_no_remote_does_not_push() -> None:
    caps = forge_mod.capabilities_of(_facts(remote_write_credential=True))

    assert caps.can_write_remote is True
    assert caps.pushes_to_external_remote is False


# ---- 分派读能力位，不读「像不像 github.com」 --------------------------------


@pytest.mark.anyio
async def test_the_app_lane_is_the_github_forge() -> None:
    got = await forge_mod.resolve(
        project_id=_pid(),
        facts=_answers(
            _facts(
                github_app_installed=True,
                has_external_remote=True,
                remote_write_credential=True,
            )
        ),
    )

    assert got.kind is forge_mod.ForgeKind.github_app
    assert got.capabilities.reports_checks is True
    assert got.declaration == ""


@pytest.mark.anyio
async def test_a_writable_non_github_remote_is_its_own_lane() -> None:
    """The middle tier: a campus GitLab, gitee, a self-hosted box.

    It reports no checks and hosts no proposal page, and it still receives every
    accepted commit — which is the whole reason it exists as a third lane rather
    than as a platform-lane project that quietly keeps the work.
    """
    got = await forge_mod.resolve(
        project_id=_pid(),
        facts=_answers(
            _facts(has_external_remote=True, remote_write_credential=True)
        ),
    )

    assert got.kind is forge_mod.ForgeKind.external_remote
    assert got.capabilities.reports_checks is False
    assert got.capabilities.hosts_proposals is False
    assert got.capabilities.pushes_to_external_remote is True
    assert got.declaration


@pytest.mark.anyio
async def test_a_project_with_nothing_bound_is_the_platform_forge() -> None:
    got = await forge_mod.resolve(project_id=_pid(), facts=_answers(_facts()))

    assert got.kind is forge_mod.ForgeKind.platform
    # 它是一个没有配 CI 的仓库，#363 说这是正当的采纳语义，不是降级——所以交付面
    # 要能说出「这里没有检查」，而不是画一个永不运行的检查步骤。
    assert got.capabilities.reports_checks is False
    assert "无外部 CI" in got.declaration


@pytest.mark.anyio
async def test_an_unreadable_project_stops_the_accept() -> None:
    """Fails closed, because the wrong guess is the one that pushes to main.

    Answering "nothing bound" on a database hiccup would send a GitHub-bound
    project down the local merge — exactly #362, arrived at by another route.
    """
    with pytest.raises(ValidationError) as caught:
        await forge_mod.resolve(project_id=_pid(), facts=_explodes)

    assert "读不出" in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "facts", [_facts(), _facts(has_external_remote=True)], ids=["bare", "remote"]
)
async def test_an_existing_proposal_keeps_its_forge_without_credentials(facts) -> None:
    got = await forge_mod.resolve(
        project_id=_pid(),
        facts=_answers(facts),
        proposal_url="https://github.com/acme/widgets/pull/42",
    )

    assert got.kind is forge_mod.ForgeKind.github_app
    assert got.capabilities.hosts_proposals is True


# ---- 注册表本身 --------------------------------------------------------------


def test_exactly_one_forge_serves_any_capabilities() -> None:
    """A capability set nobody serves, or two providers serving one, is a hole
    that only shows up as a 500 on somebody's accept. Enumerate the bits that
    select a lane and check the registry is total and disjoint over them."""
    for hosts in (True, False):
        for pushes in (True, False):
            caps = forge_mod.ForgeCapabilities(
                reports_checks=hosts,
                hosts_proposals=hosts,
                can_write_remote=pushes,
                pushes_to_external_remote=pushes,
                identity=forge_mod.ForgeIdentity.platform,
            )
            serving = [cls for cls in forge_mod.FORGES if cls.serves(caps)]
            assert len(serving) == 1, (caps, serving)


@pytest.mark.anyio
async def test_the_dispatch_names_no_provider() -> None:
    """#363's own criterion: adding a provider is adding a class.

    Resolution is a registry lookup, so a registry holding only a provider this
    module has never heard of resolves to it. Had `resolve` kept a branch per
    lane — the `is_github_bound` boolean it used to read — this could not pass,
    because the branch would still answer with a name compiled into it.
    """

    class ArchiveForge(forge_mod.Forge):
        kind = forge_mod.ForgeKind.platform
        declaration = "存档"

        @classmethod
        def serves(cls, capabilities):
            return True

        async def accept(self, service, card, topic, decided_by, *, seen_head):
            raise NotImplementedError

        async def refresh_unseen_head(self, service, card, topic, action):
            raise NotImplementedError

        async def poll(self, service, card, topic, *, chat_service, runner):
            raise NotImplementedError

        async def merge_despite_checks(
            self, service, card, topic, decided_by, *, seen_head, reason
        ):
            raise NotImplementedError

    registry = forge_mod.FORGES
    forge_mod.FORGES = (ArchiveForge,)
    try:
        got = await forge_mod.resolve(
            project_id=_pid(),
            facts=_answers(
                _facts(
                    github_app_installed=True,
                    has_external_remote=True,
                    remote_write_credential=True,
                )
            ),
        )
        assert isinstance(got, ArchiveForge)
    finally:
        forge_mod.FORGES = registry
