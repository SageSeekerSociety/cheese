"""What the forge says about a merge commit on the default branch.

The reads `landing_watch` needs, one object per project: the commit a PR's
merge put on the default branch, the checks that ran on a commit, a commit's
parent, and the first deployment that included a commit. GitHub and Forgejo
answer the first three; deployments are GitHub's, read only when the App's
installation was granted them, and a repository that records none simply has
none to report.

What is judged is what ran on the merge commit itself. The branch's required
checks are the forge's gate before the merge, which the forge has already
enforced; a check that only runs on pull requests never reports on the merge
commit, so it is not looked for there.

A repository may run its default-branch checks in batches, cancelling the run
a newer push supersedes. Such a commit's runs are cancelled, and the run that
covers it is the first one after it on the branch that was not (GitHub's
compare reads the commits after it). GitHub also supplies a batch's commits
and its failed-job logs. Forgejo reports commit statuses, which are never
cancelled, so every merge there is judged on its own.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.forge_http import forge_client
from app.domain.project.forge import binding_for_project, tokens_for_project
from app.domain.review.forge_clients import proposal_client
from app.domain.review.github_pr.failure_logs import (
    _LOG_DOWNLOAD_MAX_BYTES,
    _clean_log_line,
    _download_log_tail,
)
from app.domain.review.superseded_runs import actions_job_id

logger = logging.getLogger("cheesex.review.landing_forge")

#: What a probe run says: a verdict when it finished, ``running`` while it has not.
ProbeVerdict = Literal["pass", "fail", "unknown", "running"]


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


@dataclass(frozen=True, slots=True)
class Deployment:
    environment: str
    #: success / failure / error
    state: str
    url: str


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

    async def run_after(
        self, sha: str, names: frozenset[str], *, passed: bool = False
    ) -> tuple[str, list[Check]] | None:
        """A later run of the checks ``names`` on the branch, after ``sha``:
        the commit it reported on, and that commit's checks.

        By default the first run after ``sha`` that was not cancelled, which
        is the batch that covered it; None while that run is still going or
        has not started. ``passed`` asks instead for the newest one that went
        green, which settles a red on ``sha``: that commit contains it. None
        on a forge that cannot list the commits after one."""
        ...

    async def commit_change(self, sha: str) -> CommitChange | None:
        """What one commit changed; None when the forge has no such commit."""
        ...

    async def failed_paths(self, head: str, *, tests_only: bool = False) -> list[str]:
        """Files named by failed tests or static checks in the red jobs' logs.
        ``tests_only`` returns no paths when any static diagnostic is present:
        a test rerun cannot judge that failure, even in the same file."""
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
    if binding.kind == "github_app":
        # Empty means "not resolved from the provider yet" (`repositories.py`
        # never assumes main); there is then no branch to look ahead on.
        return GitHubLandingReads(
            client,
            tokens,
            binding.api_url,
            owner,
            repo,
            branch=binding.default_branch or None,
        )
    return ForgejoLandingReads(client, tokens, binding.api_url, owner, repo)


_FINISHED_DEPLOYMENT = ("success", "failure", "error")

#: GitHub lists at most 300 files in one commit. Past that a commit's changes
#: cannot be read whole, and a failure must not be pinned on it.
_COMMIT_MAX_FILES = 300
#: How many commits after a merge are looked at for a later run. It bounds
#: forge calls rather than express a rule: a batch is a handful of commits.
_LOOK_AHEAD = 20
#: How many red jobs get their log read for failing file names.
_RED_LOGS = 3

#: A failed check-run's log names its failing tests in one of these shapes:
#: pytest `FAILED tests/x.py::test_y - …`, vitest/jest `FAIL  src/x.test.ts > …`.
_FAILED_LINE = re.compile(r"\b(?:FAILED|ERROR|FAIL)\b|✕")
_TEST_PATH = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|pyi|ts|tsx|js|jsx|vue|go|rs|java))"
)

#: Ruff's full/concise locations, Pyright, tsc/vue-tsc, and Go vet errors.
_SOURCE_FILE = r"[^\s:()]+\.(?:py|pyi|[cm]?[jt]sx?|vue|css|scss|go)"
_STATIC_DIAGNOSTIC = re.compile(
    rf"^\s*(?:-->\s+)?({_SOURCE_FILE})(?:"
    r":\d+:\d+(?:\s*$|:\s*[A-Z]+\d+\b|\s+-\s+error:)"
    r"|\(\d+,\d+\):\s*error TS\d+\b"
    r"|(?<=\.go):\d+:\d+:\s+\S"
    r"|:\d+:\d+\s{2,}\S|:\d+\s+\S"
    r"|:\s+(?:[B-Z]|A -> [B-Z])\s*$)"
)
#: Grouped diagnostics put the path above location/severity or issue-label rows.
_DIAGNOSTIC_FILE = re.compile(rf"^\s*({_SOURCE_FILE})\s*$")
_GROUPED_ERROR = re.compile(r"^\s+(?:\d+:\d+\s+error\s|\d+:\s+\[[^]]+\]\s)")
_TAGGED_DIAGNOSTIC = re.compile(r"^\s+(?:NEW|BASELINE GREW):\s+(.+)$")

#: A completed check that went red. `cancelled` is not one: a newer push to
#: main cancels the run it superseded.
_RED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "startup_failure", "action_required", "stale"}
)

#: A completed check that passed, as `merge_state` reads it.
_PASS_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def named_runs(runs: list[Check], names: frozenset[str]) -> Check | None:
    """What the checks ``names`` came to on one commit, as one check; None
    when one of them has no run there.

    A re-run leaves more than one run of the same name on a commit; as
    `merge_state._outcome` reads them, any pass wins, a run still going beats
    an old red, and only then is the remaining one the answer."""
    selected = []
    for name in sorted(names):
        same = [run for run in runs if run.name == name]
        if not same:
            return None
        selected.append(
            next(
                (
                    run
                    for run in same
                    if run.status == "completed" and run.conclusion in _PASS_CONCLUSIONS
                ),
                next(
                    (run for run in same if run.status != "completed"),
                    next(
                        (run for run in same if run.conclusion in _RED_CONCLUSIONS),
                        same[0],
                    ),
                ),
            )
        )
    if len(selected) == 1:
        return selected[0]
    pending = any(run.status != "completed" for run in selected)
    red = any(run.conclusion in _RED_CONCLUSIONS for run in selected)
    cancelled = any(run.conclusion == "cancelled" for run in selected)
    return Check(
        ", ".join(run.name for run in selected),
        "in_progress" if pending else "completed",
        None
        if pending
        else "failure"
        if red
        else "cancelled"
        if cancelled
        else "success",
        next((run.url for run in selected if run.url), ""),
    )


def _failed_paths(log: str, *, tests_only: bool = False) -> list[str]:
    """Files named by test failures and static diagnostics in a job log.

    Test probes must use ``tests_only``: rerunning a test file cannot judge a
    lint or type error in it.
    """
    found: list[str] = []
    diagnostic_file: str | None = None
    failure_report = False
    for raw in log.splitlines():
        line = _clean_log_line(raw)
        if _FAILED_LINE.search(line):
            found += [match.group(1) for match in _TEST_PATH.finditer(line)]
            failure_report = True
        elif not line.strip():
            failure_report = False
        if tests_only:
            continue
        diagnostic = _STATIC_DIAGNOSTIC.match(line)
        if diagnostic:
            found.append(diagnostic.group(1))
        tagged = _TAGGED_DIAGNOSTIC.match(line) if failure_report else None
        if tagged:
            found += [match.group(1) for match in _TEST_PATH.finditer(tagged.group(1))]
        header = _DIAGNOSTIC_FILE.fullmatch(line)
        if header:
            diagnostic_file = header.group(1)
            if diagnostic_file and diagnostic_file.endswith(".go"):
                # gofmt -l reports an unformatted file as a bare path.
                found.append(diagnostic_file)
        elif diagnostic_file and _GROUPED_ERROR.match(line):
            found.append(diagnostic_file)
        elif not re.match(r"^\s+\d+:\d+\s+warning\s", line):
            diagnostic_file = None
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
                )
                for run in runs
            ]
            if len(runs) < 100:
                break
            page += 1
        page = 1
        while True:
            data = await self._get(
                f"/commits/{sha}/status", {"per_page": 100, "page": page}
            )
            statuses = data.get("statuses") or []
            found += [
                Check(
                    status["context"],
                    "completed" if status["state"] != "pending" else "in_progress",
                    {
                        "success": "success",
                        "failure": "failure",
                        "error": "failure",
                    }.get(status["state"]),
                    status.get("target_url") or "",
                )
                for status in statuses
            ]
            if len(statuses) < 100:
                return found
            page += 1

    async def parent(self, sha: str) -> str | None:
        parents = (await self._get(f"/commits/{sha}")).get("parents") or []
        return parents[0].get("sha") if parents else None

    # --- a branch checked in batches (#4249) ------------------------------

    async def run_after(
        self, sha: str, names: frozenset[str], *, passed: bool = False
    ) -> tuple[str, list[Check]] | None:
        if not self._branch:
            return None
        try:
            data = await self._get(
                f"/compare/{quote(sha, safe='')}...{quote(self._branch, safe='')}"
            )
        except ForgeUnavailable:
            return None
        if data.get("status") != "ahead":
            return None
        later = [
            commit["sha"]
            for commit in data.get("commits") or []
            if isinstance(commit, dict) and isinstance(commit.get("sha"), str)
        ]
        # Oldest first, as the compare lists them. The covering run is the
        # first that was not cancelled; one still going is the answer to wait
        # for. A green one is looked for from the other end, past whatever
        # has not finished or went red since.
        candidates = reversed(later[-_LOOK_AHEAD:]) if passed else later[:_LOOK_AHEAD]
        for commit in candidates:
            runs = await self.checks(commit)
            ran = named_runs(runs, names)
            if ran is None or ran.conclusion == "cancelled":
                continue
            if passed:
                if ran.status == "completed" and ran.conclusion in _PASS_CONCLUSIONS:
                    return commit, runs
                continue
            return (commit, runs) if ran.status == "completed" else None
        return None

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

    async def failed_paths(self, head: str, *, tests_only: bool = False) -> list[str]:
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
            log = await self._job_log(job)
            paths = _failed_paths(log)
            if tests_only:
                tests = _failed_paths(log, tests_only=True)
                if paths != tests:
                    return []
                paths = tests
            found += paths
        return sorted(set(found))

    async def _job_log(self, job_id: int) -> str:
        """A failed Actions job's bounded log, or "" when it cannot be read."""
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
                    # Static checks can fail before a long test collection;
                    # attribution needs those early diagnostics too.
                    return await _download_log_tail(
                        http, location, keep_bytes=_LOG_DOWNLOAD_MAX_BYTES
                    )
                if resp.status_code == 200:
                    return resp.text[:_LOG_DOWNLOAD_MAX_BYTES]
        except Exception as exc:  # a log that cannot be read attributes nothing
            logger.info("could not read job %s log: %s", job_id, exc)
        return ""

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

    async def _includes(self, deployed: str, sha: str) -> bool:
        if deployed == sha:
            return True
        compared = await self._get(f"/compare/{sha}...{deployed}")
        return compared.get("status") in ("ahead", "identical")


class ForgejoLandingReads:
    def __init__(self, client, tokens, api_base: str, owner: str, repo: str):
        self._client = client
        self._tokens = tokens
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

    # Forgejo reports commit statuses, which are never cancelled: each merge
    # is judged on its own, and there is no batch to read.

    async def run_after(
        self, sha: str, names: frozenset[str], *, passed: bool = False
    ) -> tuple[str, list[Check]] | None:
        return None

    async def commit_change(self, sha: str) -> CommitChange | None:
        return None

    async def failed_paths(self, head: str, *, tests_only: bool = False) -> list[str]:
        return []
