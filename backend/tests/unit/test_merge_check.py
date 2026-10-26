"""Whether a PR's head merges into its base is git's answer, not a guess.

Forgejo answers `mergeable: false` while its merge check is still running as
well as on a conflict. The platform settles it with the test merge itself: a
head that merges cleanly into a base that moved on is clean, one that changed
the same line is a conflict, and without an answer (git could not reach the
repository) there is none — never a conflict.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.core import background
from app.core.config import settings
from app.domain.review import merge_check
from app.domain.review.forgejo_pr import ForgejoClient
from tests.support.git_origin import proposal, proposals_over_time, repositories


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


#: A cap small enough that one repository's cache is over it.
_SMALL_CAP = 20 * 1024


def _bytes_on_disk(path) -> int:
    """What everything under ``path`` takes on disk, in whole blocks."""
    return sum(f.lstat().st_blocks * 512 for f in path.rglob("*"))


class _Killed(BaseException):
    """The process running the check dies here."""


def test_a_check_cut_short_while_setting_up_does_not_strand_the_repository(
    tmp_path, monkeypatch
):
    pr = proposal(tmp_path / "repo", conflicting=False)
    real = merge_check._git

    def dies_while_configuring(cache, args, env, timeout):
        if args[0] == "config":
            raise _Killed
        return real(cache, args, env, timeout)

    monkeypatch.setattr(merge_check, "_git", dies_while_configuring)
    with pytest.raises(_Killed):
        merge_check.decide(pr.url, "token", pr.base, pr.head)
    monkeypatch.setattr(merge_check, "_git", real)

    assert merge_check.decide(pr.url, "token", pr.base, pr.head) == "clean"


def test_a_repository_larger_than_the_cap_is_checked_but_not_kept(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(merge_check, "CACHE_LIMIT_BYTES", _SMALL_CAP)
    pr = proposal(tmp_path / "repo", conflicting=False)

    assert merge_check.decide(pr.url, "token", pr.base, pr.head) == "clean"
    assert merge_check.answer(pr.url, pr.base, pr.head) == "clean"
    assert _bytes_on_disk(tmp_path / "workspace") <= _SMALL_CAP


def test_a_check_that_fails_still_keeps_the_caches_under_the_cap(tmp_path, monkeypatch):
    earlier = proposal(tmp_path / "earlier", conflicting=False)
    assert merge_check.decide(earlier.url, "t", earlier.base, earlier.head) == "clean"
    monkeypatch.setattr(merge_check, "CACHE_LIMIT_BYTES", _SMALL_CAP)

    missing = (tmp_path / "nowhere.git").as_uri()
    assert merge_check.decide(missing, "t", "a" * 40, "b" * 40) is None
    assert _bytes_on_disk(tmp_path / "workspace") <= _SMALL_CAP


def test_answers_past_their_age_are_removed(tmp_path, monkeypatch):
    url, base, heads = proposals_over_time(tmp_path / "repo", 2)
    assert merge_check.decide(url, "t", base, heads[0]) == "clean"
    # From here on, every answer on disk is past the age answers are kept for.
    monkeypatch.setattr(merge_check, "ANSWERS_KEPT_FOR_S", -60)

    assert merge_check.decide(url, "t", base, heads[1]) == "clean"
    assert merge_check.answer(url, base, heads[0]) is None


def test_checks_running_at_once_under_the_cap_all_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(merge_check, "CACHE_LIMIT_BYTES", _SMALL_CAP)
    prs = repositories(tmp_path, 24)
    with ThreadPoolExecutor(max_workers=len(prs)) as pool:
        answers = list(
            pool.map(lambda pr: merge_check.decide(pr.url, "t", pr.base, pr.head), prs)
        )
    assert answers == ["clean"] * len(prs)


def test_an_answer_outlives_other_repositories_checks_under_the_cap(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(merge_check, "CACHE_LIMIT_BYTES", _SMALL_CAP)
    a = proposal(tmp_path / "a", conflicting=True)
    b = proposal(tmp_path / "b", conflicting=False)

    assert merge_check.decide(a.url, "t", a.base, a.head) == "conflict"
    assert merge_check.decide(b.url, "t", b.base, b.head) == "clean"
    assert merge_check.answer(a.url, a.base, a.head) == "conflict"


def test_an_answer_being_written_survives_another_check(tmp_path, monkeypatch):
    monkeypatch.setattr(merge_check, "CACHE_LIMIT_BYTES", _SMALL_CAP)
    a = proposal(tmp_path / "a", conflicting=True)
    b = proposal(tmp_path / "b", conflicting=False)
    written, go_on = threading.Event(), threading.Event()
    write_text = Path.write_text

    def pauses_after_the_first_partial_answer(self, *args, **kwargs):
        wrote = write_text(self, *args, **kwargs)
        if self.name.endswith(".partial") and not written.is_set():
            written.set()
            go_on.wait(30)
        return wrote

    monkeypatch.setattr(Path, "write_text", pauses_after_the_first_partial_answer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(merge_check.decide, a.url, "t", a.base, a.head)
        assert written.wait(30)
        assert merge_check.decide(b.url, "t", b.base, b.head) == "clean"
        go_on.set()
        assert first.result(30) == "conflict"
    assert merge_check.answer(a.url, a.base, a.head) == "conflict"


def test_pruning_never_removes_where_another_check_is_about_to_write(
    tmp_path, monkeypatch
):
    a = proposal(tmp_path / "a", conflicting=True)
    b = proposal(tmp_path / "b", conflicting=False)
    # Every answer on disk counts as past its age, so B's check prunes all it can.
    monkeypatch.setattr(merge_check, "ANSWERS_KEPT_FOR_S", -60)
    made, go_on = threading.Event(), threading.Event()
    mkdir = Path.mkdir
    depth = threading.local()

    def pauses_after_making_the_answer_folder(self, *args, **kwargs):
        # `mkdir(parents=True)` calls itself for each missing parent: pause once
        # the whole folder the answer goes into exists, not partway.
        depth.n = getattr(depth, "n", 0) + 1
        try:
            mkdir(self, *args, **kwargs)
        finally:
            depth.n -= 1
        is_answers = "answers" in self.parts
        if depth.n == 0 and is_answers and not made.is_set():
            made.set()
            go_on.wait(30)

    monkeypatch.setattr(Path, "mkdir", pauses_after_making_the_answer_folder)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(merge_check.decide, a.url, "t", a.base, a.head)
        assert made.wait(30)
        assert merge_check.decide(b.url, "t", b.base, b.head) == "clean"
        go_on.set()
        assert first.result(30) == "conflict"
