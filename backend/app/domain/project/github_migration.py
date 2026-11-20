"""Copy a platform-hosted (Forgejo) repository into an empty GitHub repository.

A project made on the platform's own forge — every project a 题目 claim opens
is one — can move to a GitHub repository its people own. This module is the git
half: every branch and tag of the Forgejo repository is pushed, unchanged, to
the GitHub repository, and nothing there is ever replaced. The binding switch
and what it means for open tasks and cards is the route's half
(``api/routes/github_migration.py``).

Only ``refs/heads/*`` and ``refs/tags/*`` move. Forgejo keeps its own
``refs/pull/*`` for its PRs; GitHub refuses pushes to that namespace, and the
PRs themselves do not move (open tasks get new ones on GitHub).

The destination must hold nothing this copy would not also write: a ref there
that differs from the source refuses the whole copy before anything is pushed.
A copy that stopped halfway is therefore resumed by asking again.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from app.core.errors import ConflictError, GatewayUnavailableError
from app.core.sentences import say
from app.domain.project.starter import Remote

log = logging.getLogger(__name__)

_MOVED = ("refs/heads/", "refs/tags/")


def _git(work: Path, *args: str, env: dict) -> str:
    result = subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    if result.returncode:
        # stderr may carry an address; it goes to the log, not to the person.
        log.warning(
            "github migration git %s failed: %s", args[0], result.stderr.strip()
        )
        raise GatewayUnavailableError(say("githubMigrationPushFailed"))
    return result.stdout


def _token_env(token: str) -> dict:
    """An installation token as GitHub's git endpoint takes it, in a request
    header rather than the address or a config file."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": "Authorization: Basic " + basic,
    }


def _remote_refs(work: Path, url: str, env: dict) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in _git(work, "ls-remote", "--refs", url, env=env).splitlines():
        sha, ref = line.split("\t", 1)
        if ref.startswith(_MOVED):
            refs[ref] = sha
    return refs


def _source_refs(work: Path, source: Remote) -> dict[str, str]:
    """Fetch the source's branches and tags into ``work``; return them."""
    env = source.env()
    _git(
        work,
        "fetch",
        "-q",
        "--prune",
        source.url,
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
        env=env,
    )
    return {
        ref: sha
        for ref, sha in (
            line.split(" ", 1)
            for line in _git(
                work, "for-each-ref", "--format=%(refname) %(objectname)", env=env
            ).splitlines()
        )
        if ref.startswith(_MOVED)
    }


#: A push landing on the source while it is copied is copied by another round;
#: a source that keeps moving after this many is given up on, saying so.
ROUNDS = 3


def mirror_to_github(source: Remote, destination: str, token: str) -> dict[str, str]:
    """Push every branch and tag of ``source`` to ``destination``; return the
    refs the destination holds afterwards (equal to the source's).

    Refused, with nothing pushed, when the destination already has a branch or
    tag that is neither the source's nor one this copy wrote — an empty
    repository, or one an earlier attempt partly filled, is what this accepts.
    A ref this copy wrote is moved on with a lease on what it wrote, so a push
    made on GitHub meanwhile is never overwritten."""
    target_env = _token_env(token)
    ours: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="github-migration-") as temporary:
        work = Path(temporary) / "repository.git"
        _git(Path(temporary), "init", "-q", "--bare", str(work), env=source.env())
        for _ in range(ROUNDS):
            expected = _source_refs(work, source)
            if not expected:
                raise ConflictError(say("githubMigrationSourceEmpty"))
            before = _remote_refs(work, destination, target_env)
            foreign = [
                ref
                for ref, sha in before.items()
                if sha != expected.get(ref) and sha != ours.get(ref)
            ]
            if foreign:
                raise ConflictError(say("githubMigrationTargetNotEmpty"))
            moves = [ref for ref, sha in expected.items() if before.get(ref) != sha]
            if not moves:
                return before
            leases = [
                f"--force-with-lease={ref}:{before[ref]}"
                for ref in moves
                if ref in before
            ]
            _git(
                work,
                "push",
                "-q",
                "--atomic",
                *leases,
                destination,
                *(
                    f"+{ref}:{ref}" if ref in before else f"{ref}:{ref}"
                    for ref in moves
                ),
                env=target_env,
            )
            ours.update({ref: expected[ref] for ref in moves})
    log.warning("github migration source kept moving after %d rounds", ROUNDS)
    raise GatewayUnavailableError(say("githubMigrationPushFailed"))
