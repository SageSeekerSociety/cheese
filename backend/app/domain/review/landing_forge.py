"""What the forge says about a merge commit on the default branch.

The reads `landing_watch` needs, one object per project: the commit a PR's
merge put on the default branch, the checks that ran on a commit, a commit's
parent, and the first deployment that included a commit. GitHub and Forgejo
answer the first three; deployments are GitHub's, read only when the App's
installation was granted them, and a repository that records none simply has
none to report.

GitHub also answers the four reads a batched main gate needs (#4249): the run
whose gate covers a merge commit, the commit `main-green` names, the commits a
batch covers and what each changed, and the tests a red job's log names.
Forgejo answers none of them: it gates each merge on its own, and a commit
with no runs there has simply not been checked yet.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.forge_http import forge_client
from app.domain.project.forge import binding_for_project, tokens_for_project
from app.domain.review.forge_clients import proposal_client
from app.domain.review.github_pr.failure_logs import (
    _clean_log_line,
    _download_log_tail,
)
from app.domain.review.superseded_runs import actions_job_id

logger = logging.getLogger("cheesex.review.landing_forge")


class ForgeUnavailable(Exception):
    """The forge will not say: the repository or the permission is not there."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    #: queued / in_progress / completed
    status: str
    #: success / failure / cancelled / skipped / timed_out / … once completed
    conclusion: str | None
    url: str
    #: The check suite (one workflow run) the check belongs to. A workflow
    #: that runs beside the batched gate — an image build, say — is another
    #: suite, which is how its failure is told apart from the gate's own.
    suite: int | None = None


@dataclass(frozen=True, slots=True)
class Deployment:
    environment: str
    #: success / failure / error
    state: str
    url: str


#: The check run the batched main gate aggregates under: a literal in
#: `required-ci.yml`, and the name branch protection requires (#4249).
BATCH_GATE = "CI required"

#: Where the batched gate's last green result is kept. `required-ci.yml` moves
#: it after a green run on main; a red one leaves it, so the next run covers
#: both.
MAIN_GREEN_REF = "main-green"


@dataclass(frozen=True, slots=True)
class CommitChange:
    """One commit a batch covers, and what it changed."""

    sha: str
    subject: str
    files: tuple[str, ...]
    #: False when the forge may have listed only part of what the commit
    #: changed (GitHub stops at 300 files), so nothing can be pinned on it.
    complete: bool = True


@dataclass(frozen=True, slots=True)
class BatchCommits:
    """The commits a batch covers, as far as the forge would list them."""

    #: How many commits the batch covers, whether or not each was read.
    total: int
    #: The commits whose changes were read: the newest ones, when the forge
    #: refused to list them all.
    changes: tuple[CommitChange, ...]
    #: False when some commits were not read, so no failure can be pinned on
    #: one commit.
    complete: bool


class LandingReads(Protocol):
    async def merge_sha(self, pr_number: int) -> str | None: ...
    async def checks(self, sha: str) -> list[Check]: ...
    async def parent(self, sha: str) -> str | None: ...
    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        """The first finished deployment since ``since`` that includes ``sha``;
        None while there is none yet. Raises `ForgeUnavailable` when the forge
        records none the platform may read."""
        ...

    async def covering_run(
        self, sha: str, *, anchor: str | None = None
    ) -> tuple[str, list[Check]] | None:
        """The newest finished batch gate at or after ``sha`` — the commit the
        run reported on, and that commit's checks. ``anchor`` is what
        `main_green` names, when it names anything: with a mark to find, the
        search may go further back. None while no gate has finished yet, and
        on a forge that gates each merge on its own."""
        ...

    async def main_green(self) -> str | None:
        """The commit the last green batch ended on, or None when the forge
        keeps no such mark."""
        ...

    async def commits_between(self, base: str, head: str) -> BatchCommits | None:
        """The commits after ``base`` up to and including ``head``; None when
        the forge cannot compare them (``base`` is not an ancestor, say)."""
        ...

    async def commit_change(self, sha: str) -> CommitChange | None:
        """What one commit changed; None when the forge has no such commit."""
        ...

    async def failed_tests(self, head: str) -> list[str]:
        """The test files the red jobs on ``head`` name in their logs."""
        ...

    async def is_ancestor(self, commit: str, descendant: str) -> bool:
        """Whether ``descendant`` is ``commit`` or comes after it."""
        ...

    async def dispatch_bisect(
        self, sha: str, tests: list[str], nonce: str
    ) -> str | None:
        """Rerun ``tests`` on ``sha`` in a culprit probe run; the run's id, or
        None when the forge cannot run one (no permission, no default branch,
        the run never showed up)."""
        ...

    async def bisect_verdict(self, run_id: str) -> str:
        """``pass``, ``fail`` or ``unknown`` for a probe run. Only the step that
        runs the named tests failing is ``fail``; a run still going, or one
        that broke anywhere else, is ``unknown``."""
        ...


async def reads_for(
    project_id: uuid.UUID, session: AsyncSession
) -> LandingReads | None:
    """The project's forge, or None when it has no repository."""
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    client = await proposal_client(project_id, session)
    tokens = await tokens_for_project(project_id, session)
    owner, repo = binding.repo.split("/", 1)
    # Empty means "not resolved from the provider yet" (`repositories.py`
    # never assumes main); the batched reads simply have no branch to compare
    # against until it is.
    branch = binding.default_branch or None
    if binding.kind == "github_app":
        return GitHubLandingReads(
            client, tokens, binding.api_url, owner, repo, branch=branch
        )
    return ForgejoLandingReads(
        client, tokens, binding.api_url, owner, repo, branch=branch
    )


_FINISHED_DEPLOYMENT = ("success", "failure", "error")

#: GitHub lists at most 250 commits in one comparison and 300 files in one
#: commit. Past those a batch cannot be read whole, and a failure must not be
#: pinned on whichever commit happened to be listed.
_COMPARE_MAX_COMMITS = 250
_COMMIT_MAX_FILES = 300
#: How far forward the covering run is looked for, and how far back a batch is
#: read when `main-green` cannot say where it starts. Both bound forge calls
#: rather than express a rule: a batch is a handful of commits.
_LOOK_AHEAD = 20
_LOOK_BACK = 20
#: How many red jobs get their log read for failed test names.
_RED_LOGS = 3
#: How far the search goes when `main-green` names nothing. Before the first
#: green batch no anchor exists, and a batch can be as long as the attribution
#: reads: the same bound applies until the mark appears.
_LOOK_AHEAD_NO_ANCHOR = 20

#: A failed check-run's log names its failing tests in one of these shapes:
#: pytest `FAILED tests/x.py::test_y - …`, vitest/jest `FAIL  src/x.test.ts > …`.
_FAILED_LINE = re.compile(r"\b(?:FAILED|ERROR|FAIL)\b|✕")
_TEST_PATH = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|pyi|ts|tsx|js|jsx|vue|go|rs|java))"
)

#: The workflow that reruns named tests on one commit, and the step in it whose
#: failure alone means "the tests fail on that commit".
BISECT_WORKFLOW = "culprit-bisect.yml"
BISECT_TEST_STEP = "run the named tests"
#: Looking for a just-dispatched run: GitHub lists it a moment late.
_RUN_LOOKUPS = 3
_RUN_LOOKUP_WAIT_S = 3.0

#: A completed check that went red. `cancelled` is not one: a newer push to
#: main cancels the run it superseded.
_RED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})

#: A completed check that passed, as `merge_state` reads it.
_PASS_CONCLUSIONS = frozenset({"success", "neutral"})


def gate_run(runs: list[Check]) -> Check | None:
    """The batched gate's own check run among a commit's checks, if any.

    A re-run leaves more than one run of the same name on a commit; as
    `merge_state._outcome` reads them, any pass wins, a run still going beats
    an old red, and only then is the remaining one the answer."""
    gates = [run for run in runs if run.name == BATCH_GATE]
    if not gates:
        return None
    for run in gates:
        if run.status == "completed" and run.conclusion in _PASS_CONCLUSIONS:
            return run
    for run in gates:
        if run.status != "completed":
            return run
    for run in gates:
        if run.conclusion in _RED_CONCLUSIONS:
            return run
    return gates[0]


def _failed_test_paths(log: str) -> list[str]:
    """The test files a failed job's log names: pytest's `FAILED tests/x.py::…`
    summary, vitest's/jest's `FAIL tests/x.test.ts > …`."""
    found: list[str] = []
    for raw in log.splitlines():
        line = _clean_log_line(raw)
        if not _FAILED_LINE.search(line):
            continue
        found += [match.group(1) for match in _TEST_PATH.finditer(line)]
    return found


class GitHubLandingReads:
    def __init__(
        self,
        client,
        tokens,
        api_base: str,
        owner: str,
        repo: str,
        transport=None,
        *,
        branch: str | None = None,
    ):
        self._client = client
        self._tokens = tokens
        self._transport = transport
        self._branch = branch
        self._base = f"{api_base.rstrip('/')}/repos/{quote(owner)}/{quote(repo)}"

    async def _get(self, path: str, params: dict | None = None):
        token, _ = await self._tokens.installation_token()
        async with forge_client(transport=self._transport, timeout=20.0) as http:
            resp = await http.get(
                self._base + path,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code in (403, 404):
            raise ForgeUnavailable
        resp.raise_for_status()
        return resp.json()

    async def merge_sha(self, pr_number: int) -> str | None:
        return (await self._client.pr_status(pr_number)).merge_commit_sha

    async def checks(self, sha: str) -> list[Check]:
        found: list[Check] = []
        page = 1
        while True:
            data = await self._get(
                f"/commits/{sha}/check-runs", {"per_page": 100, "page": page}
            )
            runs = data.get("check_runs") or []
            found += [
                Check(
                    name=run.get("name") or "",
                    status=run.get("status") or "",
                    conclusion=run.get("conclusion"),
                    url=run.get("html_url") or "",
                    suite=(run.get("check_suite") or {}).get("id"),
                )
                for run in runs
            ]
            if len(runs) < 100:
                return found
            page += 1

    async def parent(self, sha: str) -> str | None:
        parents = (await self._get(f"/commits/{sha}")).get("parents") or []
        return parents[0].get("sha") if parents else None

    # --- the batched main gate (#4249) -------------------------------------

    async def covering_run(
        self, sha: str, *, anchor: str | None = None
    ) -> tuple[str, list[Check]] | None:
        if not self._branch:
            return None
        try:
            data = await self._get(
                f"/compare/{quote(sha, safe='')}...{quote(self._branch, safe='')}"
            )
        except ForgeUnavailable:
            return None
        if data.get("status") not in ("ahead", "identical"):
            return None
        # With a mark there is a batch to find, and it may have started several
        # commits back. Without one the repository may not gate main at all,
        # and every candidate costs a check-runs call on every tick: look at
        # the newest few only.
        depth = _LOOK_AHEAD if anchor else _LOOK_AHEAD_NO_ANCHOR
        ahead: list[str] = []
        for commit in (data.get("commits") or [])[-depth:]:
            if not isinstance(commit, dict):
                continue
            head_sha = commit.get("sha")
            if isinstance(head_sha, str) and head_sha:
                ahead.append(head_sha)
        # Newest first. A gate still running is skipped rather than waited on:
        # a red run before it decided this commit's fate, and a later green one
        # takes the blame back (`_withdraw_passed`). Waiting for it would mean
        # never deciding while merges keep landing.
        for commit in reversed(ahead):
            runs = await self.checks(commit)
            gate = gate_run(runs)
            if gate is None or gate.status != "completed":
                continue
            if gate.conclusion != "cancelled":
                return commit, runs
        return None

    async def main_green(self) -> str | None:
        try:
            data = await self._get(f"/git/ref/heads/{quote(MAIN_GREEN_REF, safe='')}")
        except ForgeUnavailable:
            return None
        sha = (data.get("object") or {}).get("sha")
        return sha or None

    async def commits_between(self, base: str, head: str) -> BatchCommits | None:
        try:
            data = await self._get(
                f"/compare/{quote(base, safe='')}...{quote(head, safe='')}"
            )
        except ForgeUnavailable:
            return None
        if data.get("status") not in ("ahead", "identical"):
            return None
        listed = [c for c in (data.get("commits") or []) if isinstance(c, dict)]
        total = data.get("total_commits")
        total = total if isinstance(total, int) else len(listed)
        # Completeness is about what was *read*, not what was listed: past the
        # caps some commits are never looked at, and a commit the forge refused
        # to describe is one whose files are unknown.
        complete = (
            len(listed) >= total
            and len(listed) < _COMPARE_MAX_COMMITS
            and len(listed) <= _LOOK_BACK
        )
        changes = []
        for commit in listed[-_LOOK_BACK:]:
            sha = commit.get("sha")
            if not sha:
                complete = False
                continue
            change = await self.commit_change(sha)
            if change is None:
                complete = False
                continue
            changes.append(change)
        return BatchCommits(total=total, changes=tuple(changes), complete=complete)

    async def commit_change(self, sha: str) -> CommitChange | None:
        try:
            data = await self._get(f"/commits/{quote(sha, safe='')}")
        except ForgeUnavailable:
            return None
        message = str((data.get("commit") or {}).get("message") or "").strip()
        subject = message.splitlines()[0] if message else ""
        listed = [f for f in (data.get("files") or []) if isinstance(f, dict)]
        files = tuple(f.get("filename") or "" for f in listed if f.get("filename"))
        return CommitChange(
            sha=sha,
            subject=subject,
            files=files,
            complete=len(listed) < _COMMIT_MAX_FILES,
        )

    async def failed_tests(self, head: str) -> list[str]:
        try:
            data = await self._get(
                f"/commits/{quote(head, safe='')}/check-runs", {"per_page": 100}
            )
        except ForgeUnavailable:
            return []
        red = [
            run
            for run in (data.get("check_runs") or [])
            if isinstance(run, dict)
            and run.get("status") == "completed"
            and run.get("conclusion") in _RED_CONCLUSIONS
        ]
        found: list[str] = []
        for run in red[:_RED_LOGS]:
            job = actions_job_id(run)
            if job is None:
                continue
            found += _failed_test_paths(await self._job_log(job))
        return sorted(set(found))

    async def _job_log(self, job_id: int) -> str:
        """A failed Actions job's log tail, or "" when it cannot be read."""
        try:
            token, _ = await self._tokens.installation_token()
            async with forge_client(transport=self._transport, timeout=20.0) as http:
                resp = await http.get(
                    f"{self._base}/actions/jobs/{job_id}/logs",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if not location:
                        return ""
                    # The redirect goes to a third-party blob host; the
                    # pre-signed URL needs no Authorization, and replaying it
                    # to another host is exactly what must not happen.
                    return await _download_log_tail(http, location)
                if resp.status_code == 200:
                    return resp.text[-256 * 1024 :]
        except Exception as exc:  # a log that cannot be read attributes nothing
            logger.info("could not read job %s log: %s", job_id, exc)
        return ""

    async def is_ancestor(self, commit: str, descendant: str) -> bool:
        if commit == descendant:
            return True
        try:
            data = await self._get(
                f"/compare/{quote(commit, safe='')}...{quote(descendant, safe='')}"
            )
        except ForgeUnavailable:
            return False
        return data.get("status") in ("ahead", "identical")

    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        if "deployments" not in await self._tokens.granted_permissions():
            raise ForgeUnavailable
        recent = await self._get("/deployments", {"per_page": 30})
        later = [
            d
            for d in recent
            if datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")) >= since
        ]
        # Oldest first. A later deployment that went through settles a failed
        # one before it: the commit is deployed.
        failed: Deployment | None = None
        for d in sorted(later, key=lambda d: d["created_at"]):
            if not await self._includes(d["sha"], sha):
                continue
            statuses = await self._get(
                f"/deployments/{d['id']}/statuses", {"per_page": 1}
            )
            state = statuses[0]["state"] if statuses else "pending"
            if state not in _FINISHED_DEPLOYMENT:
                return None
            seen = Deployment(
                environment=d.get("environment") or "",
                state=state,
                url=statuses[0].get("log_url") or statuses[0].get("target_url") or "",
            )
            if state == "success":
                return seen
            failed = failed or seen
        return failed

    async def dispatch_bisect(
        self, sha: str, tests: list[str], nonce: str
    ) -> str | None:
        if not self._branch:
            return None
        # Ask first: without `actions: write` the POST is a 403 the App would
        # be counted for.
        if (await self._tokens.granted_permissions()).get("actions") != "write":
            return None
        token, _ = await self._tokens.installation_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        async with forge_client(transport=self._transport, timeout=20.0) as http:
            resp = await http.post(
                f"{self._base}/actions/workflows/{BISECT_WORKFLOW}/dispatches",
                headers=headers,
                # `ref` must be a branch: a bare commit cannot be dispatched.
                json={
                    "ref": self._branch,
                    "inputs": {"sha": sha, "tests": " ".join(tests), "nonce": nonce},
                },
            )
        if resp.status_code != 204:
            return None
        # The run's head is the branch's, so the nonce in its name is the only
        # thing that ties it to this dispatch.
        for attempt in range(_RUN_LOOKUPS):
            if attempt:
                await asyncio.sleep(_RUN_LOOKUP_WAIT_S)
            try:
                data = await self._get(
                    f"/actions/workflows/{BISECT_WORKFLOW}/runs",
                    {"event": "workflow_dispatch", "per_page": 30},
                )
            except ForgeUnavailable:
                return None
            for run in data.get("workflow_runs") or []:
                # `run-name` comes back as `display_title`; `name` is the
                # workflow's own name, and only a run without a `run-name`
                # shows its title there.
                titles = (run.get("display_title"), run.get("name"))
                if f"culprit {nonce}" in titles and run.get("id"):
                    return str(run["id"])
        return None

    async def bisect_verdict(self, run_id: str) -> str:
        try:
            run = await self._get(f"/actions/runs/{quote(run_id, safe='')}")
            if run.get("status") != "completed":
                return "unknown"
            data = await self._get(
                f"/actions/runs/{quote(run_id, safe='')}/jobs", {"per_page": 100}
            )
        except ForgeUnavailable:
            return "unknown"
        conclusions = [
            step.get("conclusion")
            for job in data.get("jobs") or []
            for step in job.get("steps") or []
            if step.get("name") == BISECT_TEST_STEP
        ]
        if "failure" in conclusions:
            return "fail"
        if "success" in conclusions:
            return "pass"
        return "unknown"

    async def _includes(self, deployed: str, sha: str) -> bool:
        if deployed == sha:
            return True
        compared = await self._get(f"/compare/{sha}...{deployed}")
        return compared.get("status") in ("ahead", "identical")


class ForgejoLandingReads:
    def __init__(
        self,
        client,
        tokens,
        api_base: str,
        owner: str,
        repo: str,
        *,
        branch: str | None = None,
    ):
        self._client = client
        self._tokens = tokens
        self._branch = branch
        self._repo = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    async def merge_sha(self, pr_number: int) -> str | None:
        return (await self._client.pr_status(pr_number)).merge_commit_sha

    async def checks(self, sha: str) -> list[Check]:
        return [
            Check(
                name=run["name"] or "",
                status=run["status"] or "",
                conclusion=run["conclusion"],
                url=run.get("url") or "",
            )
            for run in await self._client.check_runs(sha)
        ]

    async def parent(self, sha: str) -> str | None:
        token, _ = await self._tokens.installation_token()
        forgejo = self._client.client
        data = forgejo._data(
            await forgejo.request("GET", f"{self._repo}/git/commits/{sha}", token=token)
        )
        parents = (data or {}).get("parents") or []
        return parents[0].get("sha") if parents else None

    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        raise ForgeUnavailable

    # Forgejo has no batched main gate: each merge is checked on its own, so a
    # commit with no runs has simply not been checked yet.

    async def covering_run(
        self, sha: str, *, anchor: str | None = None
    ) -> tuple[str, list[Check]] | None:
        return None

    async def main_green(self) -> str | None:
        return None

    async def commits_between(self, base: str, head: str) -> BatchCommits | None:
        return None

    async def commit_change(self, sha: str) -> CommitChange | None:
        return None

    async def failed_tests(self, head: str) -> list[str]:
        return []

    async def is_ancestor(self, commit: str, descendant: str) -> bool:
        return False

    async def dispatch_bisect(
        self, sha: str, tests: list[str], nonce: str
    ) -> str | None:
        return None

    async def bisect_verdict(self, run_id: str) -> str:
        return "unknown"
