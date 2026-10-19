"""Reading a red batch: which commit a failed test points at, and which red
checks the batch's own run owns."""

from app.domain.review.landing_forge import (
    BatchCommits,
    Check,
    CommitChange,
    _failed_paths,
)
from app.domain.review.landing_watch import (
    Batch,
    BatchCommit,
    _batch_checks_failed_prompt,
    _blame,
    _names,
    _red_checks,
)


def test_a_test_file_matches_the_module_it_tests():
    assert _names(
        "backend/tests/unit/test_landing_watch.py",
        ("backend/app/domain/review/landing_watch.py",),
    )


def test_a_test_file_does_not_match_another_languages_module():
    """`foo.test.ts` next to `foo.py` is a coincidence, not a dependency."""
    assert not _names("frontend/src/foo.test.ts", ("backend/app/domain/review/foo.py",))


def _batch(*files: tuple[str, tuple[str, ...]]) -> BatchCommits:
    return BatchCommits(
        total=len(files),
        changes=tuple(
            CommitChange(sha, f"feat: {sha}", changed) for sha, changed in files
        ),
        complete=True,
    )


def test_one_commit_touching_the_tests_module_is_blamed():
    commits = _batch(("b", ("backend/a.py",)), ("c", ("backend/b.py",)))
    assert _blame(("backend/tests/test_a.py",), commits) == "b"


def test_a_ruff_failure_blames_the_only_commit_that_changed_its_file():
    paths = _failed_paths(
        "2026-10-19T02:53:25.1Z F401 [*] `sqlalchemy.select` imported but unused\n"
        "2026-10-19T02:53:25.2Z   --> tests/integration/test_app_sign_in.py:15:24\n"
    )
    commits = _batch(
        ("cd901676", ("backend/tests/integration/test_app_sign_in.py",)),
        ("b1b6cc49", ("backend/app/domain/widget.py",)),
        ("3586bd6c", ("frontend/src/SignIn.vue",)),
        ("1e3ac833", ("docs/widget.md",)),
    )
    assert _blame(tuple(paths), commits) == "cd901676"


def test_a_frontend_diagnostic_matches_a_repository_relative_change():
    assert _names("src/views/SignIn.vue", ("frontend/src/views/SignIn.vue",))
    assert _names(
        "/home/runner/work/cheese/cheese/frontend/src/views/SignIn.vue",
        ("frontend/src/views/SignIn.vue",),
    )


def test_a_cli_diagnostic_matches_a_repository_relative_change():
    assert _names("./go-diagnostic.go", ("cli/go-diagnostic.go",))
    assert _names("internal/device/device.go", ("cli/internal/device/device.go",))


def test_two_commits_touching_it_blame_nobody():
    commits = _batch(("b", ("backend/a.py",)), ("c", ("backend/a.py",)))
    assert _blame(("backend/tests/test_a.py",), commits) is None


def test_a_batch_that_was_not_read_whole_blames_nobody():
    commits = BatchCommits(
        total=30,
        changes=(CommitChange("b", "feat: b", ("backend/a.py",)),),
        complete=False,
    )
    assert _blame(("backend/tests/test_a.py",), commits) is None


def test_a_rerun_that_passed_is_not_red():
    runs = [
        Check("backend tests (unit)", "completed", "failure", ""),
        Check("backend tests (unit)", "completed", "success", ""),
    ]
    assert _red_checks(runs) == []


def test_a_batch_whose_commits_were_not_all_read_says_at_least():
    batch = Batch(
        head="d",
        total=3,
        complete=False,
        commits=(BatchCommit("b", "feat: b"),),
        failed_paths=(),
        blamed=None,
    )
    prompt = _batch_checks_failed_prompt(
        title="t",
        task_id="id",
        sha="b",
        failed=[("CI required", "u")],
        reopened=False,
        batch=batch,
    )
    assert "至少 3 个提交" in prompt
    assert "各自排除" in prompt


def _prompt(batch: Batch) -> str:
    return _batch_checks_failed_prompt(
        title="t",
        task_id="id",
        sha="b",
        failed=[("CI required", "u")],
        reopened=False,
        batch=batch,
    )


def test_a_bisected_blame_says_the_search_named_this_commit():
    """The file match and a probe run are different evidence; the task is told
    which one named it, so it does not look for a file change that is not there."""
    batch = Batch(
        head="d",
        total=6,
        complete=True,
        commits=(BatchCommit("b", "feat: b"),),
        failed_paths=("tests/unit/test_x.py",),
        blamed="b",
        by_bisect=True,
    )
    prompt = _prompt(batch)
    assert "逐个重跑" in prompt
    assert "失败文件只有这条线" not in prompt


def test_a_search_that_named_nobody_says_so_and_why():
    batch = Batch(
        head="d",
        total=6,
        complete=True,
        commits=(BatchCommit("b", "feat: b"),),
        failed_paths=("tests/unit/test_x.py",),
        blamed=None,
        bisect="probe budget spent",
    )
    prompt = _prompt(batch)
    assert "没能定位" in prompt
    assert "探测次数用完" in prompt
    assert "各自排除" in prompt


def test_a_neutral_run_counts_as_a_pass():
    """`merge_state` treats neutral as passing; the watcher reads it the same."""
    runs = [
        Check("shell-tests", "completed", "neutral", ""),
        Check("shell-tests", "completed", "failure", ""),
    ]
    assert _red_checks(runs) == []
