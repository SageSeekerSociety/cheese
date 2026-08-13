"""交付进度 — a project sees the steps it actually has.

The chain used to be four fixed steps for every project, two of which were this
repo's own shape rather than anyone else's. These pin the two facts that
replaced that assumption: a chain exists only while the machine is carrying
something out, and it contains a check step only if the project's forge has
checks.
"""

import uuid

import pytest

from app.domain.review import delivery
from app.domain.review.forge import Forge, ForgeKind
from app.domain.review.models import AcceptCard, AcceptStatus

_GITHUB = Forge(ForgeKind.github_app)
_PLATFORM = Forge(ForgeKind.platform)


def _card(status: AcceptStatus) -> AcceptCard:
    return AcceptCard(
        id=uuid.uuid4(), topic_id=uuid.uuid4(), reviewer_handle="alice", status=status
    )


def _keys(steps: list[dict]) -> list[str]:
    return [step["key"] for step in steps]


def _active(steps: list[dict]) -> str | None:
    return next((s["key"] for s in steps if s["state"] == "active"), None)


def test_a_github_project_waits_on_checks_then_merges() -> None:
    steps = delivery.steps_for(_card(AcceptStatus.pr_open), _GITHUB)

    assert _keys(steps) == ["accepted", "checks", "merge"]
    assert _active(steps) == "checks"


def test_a_platform_forge_project_has_no_check_step() -> None:
    """#363: an unlinked project is a repo with no CI, and that is legitimate.

    Drawing a check step there promises something that will never happen — the
    exact failure the fixed chain had, one step further along.
    """
    steps = delivery.steps_for(_card(AcceptStatus.pr_open), _PLATFORM)

    assert _keys(steps) == ["accepted", "merge"]
    assert _active(steps) == "merge"


@pytest.mark.parametrize(
    "status",
    [
        AcceptStatus.pending,
        AcceptStatus.pending_gate,
        AcceptStatus.accepted,
        AcceptStatus.rejected,
        AcceptStatus.revoked,
        AcceptStatus.conflict,
        AcceptStatus.gate_failed,
    ],
)
def test_no_chain_unless_the_machine_is_carrying_something_out(
    status: AcceptStatus,
) -> None:
    """Empty is a real answer, and the common one.

    A card waiting on a reviewer has no machine work to describe; a settled one's
    chain is over. Rendering a chain in those states is what made the old card
    show a 部署 step to a project that does not deploy.
    """
    assert delivery.steps_for(_card(status), _GITHUB) == []


def test_no_step_is_ever_left_without_a_state_or_a_label() -> None:
    for forge in (_GITHUB, _PLATFORM):
        for step in delivery.steps_for(_card(AcceptStatus.pr_open), forge):
            assert step["label"]
            assert step["state"] in ("done", "active", "todo")


def test_deployment_is_not_promised() -> None:
    """Nothing observes a card after the merge since #206, so a deploy step
    would be drawn with no mechanism behind it. `deploy-dev.yml` now names its
    environment, so GitHub records real deployments — but observing them belongs
    to the ops room (#190), and until something does, the chain must not imply
    the platform is watching."""
    for forge in (_GITHUB, _PLATFORM):
        steps = delivery.steps_for(_card(AcceptStatus.pr_open), forge)
        assert "deploy" not in _keys(steps)
