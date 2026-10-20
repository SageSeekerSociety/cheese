"""Reading a red batch: which commit a failed test points at.

The rule `landing_forge.blame` states, and the file matching it is decided by.
The platform no longer watches a merge on the default branch, but the
deployment on-call script (`.claude/scripts/main-red-owner.py`) blames a red
batch by this same rule, so it stays — and so does this.
"""

from app.domain.review.landing_forge import (
    BatchCommits,
    CommitChange,
    _failed_paths,
    blame,
    names,
    stem_of,
)


def test_a_test_file_matches_the_module_it_tests():
    assert names(
        "backend/tests/unit/test_landing_forge.py",
        ("backend/app/domain/review/landing_forge.py",),
    )
    assert stem_of("backend/tests/unit/test_widget.py") == stem_of(
        "backend/app/domain/widget.py"
    )


def test_a_test_file_does_not_match_another_languages_module():
    """`foo.test.ts` next to `foo.py` is a coincidence, not a dependency."""
    assert not names("frontend/src/foo.test.ts", ("backend/app/domain/review/foo.py",))


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
    assert blame(("backend/tests/test_a.py",), commits) == "b"


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
    assert blame(tuple(paths), commits) == "cd901676"


def test_a_frontend_diagnostic_matches_a_repository_relative_change():
    assert names("src/views/SignIn.vue", ("frontend/src/views/SignIn.vue",))
    assert names(
        "/home/runner/work/cheese/cheese/frontend/src/views/SignIn.vue",
        ("frontend/src/views/SignIn.vue",),
    )


def test_a_cli_diagnostic_matches_a_repository_relative_change():
    assert names("./go-diagnostic.go", ("cli/go-diagnostic.go",))
    assert names("internal/device/device.go", ("cli/internal/device/device.go",))


def test_two_commits_touching_it_blame_nobody():
    commits = _batch(("b", ("backend/a.py",)), ("c", ("backend/a.py",)))
    assert blame(("backend/tests/test_a.py",), commits) is None


def test_a_batch_that_was_not_read_whole_blames_nobody():
    commits = BatchCommits(
        total=30,
        changes=(CommitChange("b", "feat: b", ("backend/a.py",)),),
        complete=False,
    )
    assert blame(("backend/tests/test_a.py",), commits) is None
