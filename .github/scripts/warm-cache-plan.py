#!/usr/bin/env python3
"""Decide which dependency domains a push to main invalidated.

The workflow that saves dependency caches for main starts one job per domain.
Without a plan every push starts all six: editing `.pre-commit-config.yaml`
costs six runners to warm one uv cache.

The range read here is the push's own, `before..sha`, not `HEAD^`. A push
carries every commit since the last one this branch published, and after a
force push `before` is not an ancestor at all — it is still the base whose
difference is the push.

Renames are read with `--no-renames` so that both halves count: a file moved
out of a domain still invalidates that domain, and a file moved into one
invalidates the one it joined. A rename-aware diff would report neither side
as an add or a delete and the domain that lost the file would keep a cache
built from it.

Anything this cannot establish selects every domain. That is deliberately the
cheap direction: the jobs run whatever the answer except the literal string
`false`, so a wrong guess here costs a runner for a job that would have found
its cache, never a cold cache on the next pull request.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys

JOBS = (
    "backend",
    "harness-bins",
    "guards",
    "remote-execution-npm",
    "go",
    "pnpm",
)

# Each dependency the `push` filter watches, and the jobs that read it. Keep
# this in step with the filter: a path that triggers the workflow but matches
# nothing here selects nothing, which is the intended answer for a path added
# to the filter and then forgotten in these rules — see the control plane
# below for the two files that must never be forgotten.
RULES = (
    # The backend's lock: both architectures sync it.
    ("backend/uv.lock", frozenset({"backend", "harness-bins"})),
    (".pre-commit-config.yaml", frozenset({"guards"})),
    # The pins whose builds fill the harness caches on both architectures.
    (
        "backend/app/domain/agent/harness/claude_code/device_launch.py",
        frozenset({"backend", "harness-bins"}),
    ),
    (
        "backend/app/domain/agent/harness/claude_code/remote_execution/client.py",
        frozenset({"backend", "harness-bins"}),
    ),
    (
        "backend/app/domain/agent/harness/codex/**",
        frozenset({"backend", "harness-bins"}),
    ),
    # pi's tarball is only unpacked on arm64.
    ("backend/app/domain/agent/harness/pi/launch.py", frozenset({"harness-bins"})),
    (
        "scripts/remote_execution/package-lock.json",
        frozenset({"remote-execution-npm"}),
    ),
    ("cli/go.mod", frozenset({"go"})),
    ("cli/go.sum", frozenset({"go"})),
    ("frontend/pnpm-lock.yaml", frozenset({"pnpm"})),
    ("frontend/package.json", frozenset({"pnpm"})),
)

# These change how the plan is made, not which dependency moved, so no reading
# of the diff can say what is still warm. The workflow is already in the push
# filter; the two scripts are added to it beside this file.
CONTROL_PLANE = frozenset(
    {
        ".github/workflows/warm-caches.yml",
        ".github/scripts/warm-cache-plan.py",
        ".github/scripts/test_warm_cache_plan.py",
    }
)


class PlanError(Exception):
    """git cannot answer, so the caller warms every domain."""


def output_key(job: str) -> str:
    """The step output name for a job: hyphens cannot be referenced there."""
    return job.replace("-", "_")


def select_jobs(paths) -> set[str]:
    """The jobs the given changed paths invalidate."""
    if CONTROL_PLANE.intersection(paths):
        return set(JOBS)
    selected: set[str] = set()
    for path in paths:
        for pattern, jobs in RULES:
            if fnmatch.fnmatchcase(path, pattern):
                selected.update(jobs)
    return selected


def changed_paths(repo: str, base: str, current: str) -> list[str]:
    """Every path the range touches, renames as a delete and an add.

    An all-zero `before` is the first push of a branch: there is no commit to
    compare against, and every cache is current, so it is not a diff at all.
    """
    if not base or set(base) <= {"0"}:
        raise PlanError("the push has no base commit")
    try:
        readable = subprocess.run(
            ["git", "-C", repo, "cat-file", "-e", f"{base}^{{commit}}"],
            capture_output=True,
        )
        if readable.returncode != 0:
            raise PlanError(f"base {base} is not readable in this checkout")
        diff = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "diff",
                "--name-status",
                "-z",
                "--no-renames",
                base,
                current,
            ],
            capture_output=True,
        )
    except OSError as error:
        raise PlanError(f"git could not be run: {error}") from error
    if diff.returncode != 0:
        detail = diff.stderr.decode("utf-8", "replace").strip()
        raise PlanError(f"git diff {base}..{current} failed: {detail}")

    fields = diff.stdout.decode("utf-8", "surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise PlanError("git diff answered with an odd number of NUL fields")
    paths = []
    for status, path in zip(fields[::2], fields[1::2]):
        # A, M, D and T are what --no-renames can produce. Anything else
        # (unmerged, a copy, a format we do not know) is a range this script
        # does not understand well enough to narrow.
        if status not in {"A", "M", "D", "T"}:
            raise PlanError(f"unexpected diff status {status!r} for {path}")
        paths.append(path)
    return paths


def plan(event: str, ref: str, base: str, current: str, repo: str) -> tuple[set[str], str]:
    """The selected jobs and a one-line reason for them."""
    if event != "push":
        return set(JOBS), f"{event or 'no event'} warms every cache"
    if ref != "refs/heads/main":
        return set(JOBS), f"{ref or 'no ref'} is not main"
    try:
        paths = changed_paths(repo, base, current)
    except PlanError as error:
        return set(JOBS), str(error)
    selected = select_jobs(paths)
    return selected, f"{len(paths)} changed path(s)"


def main() -> int:
    repo = os.environ.get("REPO", ".")
    selected, reason = plan(
        os.environ.get("EVENT_NAME", ""),
        os.environ.get("REF", ""),
        os.environ.get("BASE_SHA", ""),
        os.environ.get("CURRENT_SHA", "") or "HEAD",
        repo,
    )
    lines = [
        f"{output_key(job)}={'true' if job in selected else 'false'}" for job in JOBS
    ]
    destination = os.environ.get("GITHUB_OUTPUT", "")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        sys.stdout.write("\n".join(lines) + "\n")
    print(
        f"warm caches: {', '.join(sorted(selected)) or 'none'} ({reason})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
