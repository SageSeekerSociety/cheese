"""Whether a PR's head merges into its base is git's answer, not a guess.

Forgejo answers `mergeable: false` while its merge check is still running as
well as on a conflict. The platform settles it with the test merge itself: a
head that merges cleanly into a base that moved on is clean, one that changed
the same line is a conflict, and without an answer (git could not reach the
repository) there is none — never a conflict.
"""

import asyncio

import httpx
import pytest

from app.core import background
from app.core.config import settings
from app.domain.review import merge_check
from app.domain.review.forgejo_pr import ForgejoClient
from tests.support.git_origin import proposal


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))


def test_a_head_that_merges_into_a_base_that_moved_on_is_clean(tmp_path):
    pr = proposal(tmp_path / "repo", conflicting=False)
    assert merge_check.decide(pr.url, "token", pr.base, pr.head) == "clean"
    assert merge_check.answer(pr.url, pr.base, pr.head) == "clean"


def test_a_head_that_changed_the_same_line_as_the_base_is_a_conflict(tmp_path):
    pr = proposal(tmp_path / "repo", conflicting=True)
    assert merge_check.decide(pr.url, "token", pr.base, pr.head) == "conflict"
    assert merge_check.answer(pr.url, pr.base, pr.head) == "conflict"


def test_a_repository_git_cannot_reach_leaves_no_answer(tmp_path):
    missing = (tmp_path / "nowhere.git").as_uri()
    assert merge_check.decide(missing, "token", "a" * 40, "b" * 40) is None
    assert merge_check.answer(missing, "a" * 40, "b" * 40) is None


def _forgejo_refusing(pr) -> ForgejoClient:
    """Forgejo answering `mergeable: false` on PR 7 with this base and head."""

    def server(request):
        return httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Report",
                "head": {"ref": "task", "sha": pr.head},
                "base": {"ref": "main", "sha": pr.base},
                "state": "open",
                "merged": False,
                "mergeable": False,
            },
        )

    return ForgejoClient(
        "https://forge.example/api/v1", transport=httpx.MockTransport(server)
    )


async def _settled() -> None:
    async with asyncio.timeout(30):
        while background.inflight_count():
            await asyncio.sleep(0.05)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("conflicting", "settled"), [(False, (None, "unknown")), (True, (False, "dirty"))]
)
async def test_forgejo_refusing_is_settled_by_the_test_merge(
    tmp_path, monkeypatch, conflicting, settled
):
    pr = proposal(tmp_path / "repo", conflicting=conflicting)
    client = _forgejo_refusing(pr)
    monkeypatch.setattr(ForgejoClient, "git_url", lambda self, owner, repo: pr.url)
    args = dict(owner="acme", repo="docs", number=7, token="secret")

    first = await client.pull_request_status(**args)
    assert (first.mergeable, first.mergeable_state) == (None, "unknown")
    await _settled()
    later = await client.pull_request_status(**args)
    assert (later.mergeable, later.mergeable_state) == settled
