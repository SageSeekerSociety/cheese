"""A hosted repo cannot pass landing while one of its required checks is absent."""

from types import SimpleNamespace
import uuid

from app.domain.review.landing_forge import Check
from app.domain.review.landing_watch import _judge_checks


class RocketForge:
    # The fixture exposes the policy both APIs read, so this regression can
    # exercise the old watcher against the same external-repository state.
    main_gate = SimpleNamespace(check="")
    required_checks = tuple(
        SimpleNamespace(name=name, app_id=None, pattern=False)
        for name in ("build", "tests")
    )

    async def checks(self, sha):
        return [Check("build", "completed", "success", "")]

    async def main_green(self):
        return None

    async def covering_run(self, sha, *, anchor=None):
        return None


async def test_missing_required_context_keeps_landing_pending():
    result = await _judge_checks(
        RocketForge(), "revision", project_id=uuid.uuid4(), memo={}
    )
    assert result is None
