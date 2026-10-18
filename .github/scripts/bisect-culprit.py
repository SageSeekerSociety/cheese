"""Find the commit in a red main batch that makes the failing tests red.

The landing watch cannot always tell from a failed test's file which commit of
a batch broke it; when the batch is long enough to be worth it, this bisects
the range instead. Each step checks out a midpoint commit and runs only the
test files the red jobs named, so a step costs the failing tests, not the
suite. The search is capped: a batch of n commits needs ceil(log2 n) steps,
and past `MAX_STEPS` the answer is "could not tell" rather than a guess.

Run from the repository root, on `main`, with the commit to start from:

    python3 .github/scripts/bisect-culprit.py --base A --head D \
        --tests $'tests/unit/test_a.py\\nfrontend/src/foo.test.ts'

The result is written to the step summary and published as a `bisect-culprit`
check run on the head commit, which is where the landing watch reads it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

#: The most steps one bisect spends. A batch of up to 16 commits is decided in
#: four; a longer one reports "indeterminate" rather than spending more.
MAX_STEPS = 4

#: pytest's exit code when the given files collect no test at all: the test did
#: not exist on that commit, so that commit cannot be the one that broke it.
PYTEST_NO_TESTS = 5

BACKEND_SUFFIXES = (".py",)
FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".vue")


def commits(repo: Path, base: str, head: str) -> list[str]:
    """The commits in (base, head], oldest first."""
    out = subprocess.run(
        ["git", "rev-list", "--reverse", f"{base}..{head}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def split_tests(tests: list[str]) -> tuple[list[str], list[str]]:
    """Backend (pytest) and frontend (vitest) paths, as each runner wants them.

    The logs name files from the directory their runner worked in; a path that
    still carries the top-level directory is stripped here."""
    backend = [
        test.removeprefix("backend/") for test in tests if test.endswith(BACKEND_SUFFIXES)
    ]
    frontend = [
        test.removeprefix("frontend/")
        for test in tests
        if test.endswith(FRONTEND_SUFFIXES)
    ]
    return backend, frontend


def _run(argv: list[str], cwd: Path) -> int:
    return subprocess.run(argv, cwd=cwd).returncode


def red_at(repo: Path, commit: str, tests: list[str]) -> bool:
    """Whether the named tests fail on ``commit``.

    A test file that does not exist on the commit is not a failure there: that
    commit cannot have broken a test it never had."""
    subprocess.run(
        ["git", "checkout", "--force", "--quiet", commit], cwd=repo, check=True
    )
    backend, frontend = split_tests(tests)
    if backend:
        code = _run(["uv", "run", "pytest", *backend, "-q"], repo / "backend")
        if code not in (0, PYTEST_NO_TESTS):
            return True
    if frontend:
        code = _run(
            ["pnpm", "exec", "vitest", "run", *frontend], repo / "frontend"
        )
        if code != 0:
            return True
    return False


def bisect(
    commits_in_order: list[str], is_red, *, max_steps: int = MAX_STEPS
) -> tuple[str | None, int]:
    """The first commit that is red, or None when the search cannot tell.

    ``is_red`` is called on commits; the caller supplies it so this can be
    tested without git or a test runner."""
    low, high = 0, len(commits_in_order) - 1
    steps = 0
    tested: dict[int, bool] = {}
    while low < high and steps < max_steps:
        middle = (low + high) // 2
        tested[middle] = is_red(commits_in_order[middle])
        steps += 1
        if tested[middle]:
            high = middle
        else:
            low = middle + 1
    if low != high:
        return None, steps
    if low not in tested:
        if steps >= max_steps:
            return None, steps
        tested[low] = is_red(commits_in_order[low])
        steps += 1
    return (commits_in_order[low] if tested[low] else None), steps


def publish(head: str, summary: str) -> None:
    """Write the answer where the landing watch reads it: a check run on the
    batch head. A repository without the token or permission to create one
    still gets the step summary; the watch then falls back to the batch."""
    print(f"bisect-culprit: {summary}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        Path(step_summary).write_text(f"bisect-culprit: {summary}\n")
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    body = json.dumps(
        {
            "name": "bisect-culprit",
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "output": {"title": "bisect-culprit", "summary": summary},
        }
    ).encode()
    request = urllib.request.Request(
        f"{api}/repos/{repo}/check-runs",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=20)  # noqa: S310
    except OSError as exc:  # a check run the watch cannot read falls back
        print(f"could not publish the bisect result: {exc}", file=sys.stderr)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--tests", required=True, help="one test file per line")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    tests = [line.strip() for line in args.tests.splitlines() if line.strip()]
    if not tests:
        publish(args.head, "indeterminate: no test files were named")
        return 0
    range_commits = commits(repo, args.base, args.head)
    if not range_commits:
        publish(args.head, "indeterminate: the batch has no commits")
        return 0
    culprit, steps = bisect(
        range_commits, lambda commit: red_at(repo, commit, tests)
    )
    if culprit is None:
        publish(args.head, f"indeterminate: {len(range_commits)} commits, {steps} steps")
    else:
        publish(args.head, culprit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
