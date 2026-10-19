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

A repository may run its default-branch checks in batches: a push whose run
was superseded before it started has no runs of its own, or only cancelled
ones (which forge and which concurrency setting decides). Either way it has
no verdict of its own, and the run that covers it is that of the first later
commit, along the branch's first parents, that has one. The forge's compare
names the commits after a merge; both GitHub and Forgejo list each commit's
parents there, so the order is read off the parents rather than off the
list. GitHub also supplies a batch's commit changes and its failed-job logs.
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
    #: Which attempt of the name this is: a later attempt has a larger value.
    #: A commit's checks are listed oldest attempt first.
    attempt: int = 0


def verdicts(runs: list[Check]) -> dict[str, Check]:
    """Where each check that ran on a commit stands (`standing`), by name. A
    name every attempt of which was cancelled said nothing, and is left out:
    a commit with no verdicts has no run of its own."""
    by_name: dict[str, list[Check]] = {}
    for run in runs:
        by_name.setdefault(run.name, []).append(run)
    return {
        name: now
        for name, attempts in by_name.items()
        if (now := standing(attempts)).conclusion != "cancelled"
    }


def standing(attempts: list[Check]) -> Check:
    """Where one check name stands across its attempts on a commit, oldest
    first: the latest attempt that was not cancelled. A cancelled re-run says
    nothing about the run before it; only when every attempt was cancelled is
    the name cancelled."""
    said = [run for run in attempts if run.conclusion != "cancelled"]
    return (said or attempts)[-1]


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

    async def later(self, sha: str) -> list[str]:
        """The commits after ``sha`` on the default branch's first-parent
        line, oldest first, at most `LOOK_AHEAD` of them; empty on a forge
        that cannot say."""
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
    # Empty means "not resolved from the provider yet" (`repositories.py`
    # never assumes main); there is then no branch to look ahead on.
    if binding.kind == "github_app":
        return GitHubLandingReads(
            client,
            tokens,
            binding.api_url,
            owner,
            repo,
            branch=binding.default_branch or None,
        )
    return ForgejoLandingReads(
        client,
        tokens,
        binding.api_url,
        owner,
        repo,
        branch=binding.default_branch or None,
    )


_FINISHED_DEPLOYMENT = ("success", "failure", "error")

#: GitHub lists at most 300 files in one commit. Past that a commit's changes
#: cannot be read whole, and a failure must not be pinned on it.
_COMMIT_MAX_FILES = 300
#: How many commits after a merge are looked at for a later run. It bounds
#: forge calls rather than express a rule: a batch is a handful of commits.
LOOK_AHEAD = 20
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


def _first_parents_after(commits: list[dict], sha: str) -> list[str]:
    """The branch's first-parent line after ``sha``, oldest first, out of a
    compare's commits (each naming its parents). A side branch merged in is
    not on it: its commits were checked on their own branch, not here."""
    first_parent = {
        commit["sha"]: (commit.get("parents") or [{}])[0].get("sha")
        for commit in commits
        if isinstance(commit, dict) and isinstance(commit.get("sha"), str)
    }
    # The tip is the one commit no other names as a parent of any kind.
    named = {
        parent.get("sha")
        for commit in commits
        if isinstance(commit, dict)
        for parent in commit.get("parents") or []
    }
    tips = [commit for commit in first_parent if commit not in named]
    if len(tips) != 1:
        return []
    line, current = [], tips[0]
    while current in first_parent and current != sha:
        line.append(current)
        current = first_parent[current]
    return list(reversed(line)) if current == sha else []


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
        """Every attempt of every check on ``sha``, oldest first. GitHub lists
        only the latest run of a name unless asked for all of them (`filter`),
        and the combined status only the latest per context: a re-run still
        in flight would hide the failure it is re-running."""
        found: list[Check] = []
        page = 1
        while True:
            data = await self._get(
                f"/commits/{sha}/check-runs",
                {"filter": "all", "per_page": 100, "page": page},
            )
            runs = data.get("check_runs") or []
            found += [
                Check(
                    name=run.get("name") or "",
                    status=run.get("status") or "",
                    conclusion=run.get("conclusion"),
                    url=run.get("html_url") or "",
                    attempt=int(run.get("id") or 0),
                )
                for run in runs
            ]
            if len(runs) < 100:
                break
            page += 1
        page = 1
        while True:
            statuses = await self._get(
                f"/commits/{sha}/statuses", {"per_page": 100, "page": page}
            )
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
                    attempt=int(status.get("id") or 0),
                )
                for status in statuses
            ]
            if len(statuses) < 100:
                return sorted(found, key=lambda run: run.attempt)
            page += 1

    async def parent(self, sha: str) -> str | None:
        parents = (await self._get(f"/commits/{sha}")).get("parents") or []
        return parents[0].get("sha") if parents else None

    # --- a branch checked in batches (#4249) ------------------------------

    async def later(self, sha: str) -> list[str]:
        if not self._branch:
            return []
        try:
            data = await self._get(
                f"/compare/{quote(sha, safe='')}...{quote(self._branch, safe='')}"
            )
        except ForgeUnavailable:
            return []
        if data.get("status") != "ahead":
            return []
        return _first_parents_after(data.get("commits") or [], sha)[:LOOK_AHEAD]

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
        """Every status posted on ``sha``, oldest first: a context's earlier
        statuses are its earlier attempts, which a re-run still in flight
        must not hide."""
        token, _ = await self._tokens.installation_token()
        forgejo = self._client.client
        rows = await forgejo.pages(
            f"{self._repo}/commits/{quote(sha, safe='')}/statuses", token=token
        )
        return [
            Check(
                name=row["context"],
                status="in_progress" if row["status"] == "pending" else "completed",
                conclusion={
                    "success": "success",
                    "failure": "failure",
                    "error": "failure",
                }.get(row["status"]),
                url=row.get("target_url") or "",
                attempt=int(row["id"]),
            )
            for row in sorted(rows, key=lambda row: row["id"])
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

    async def later(self, sha: str) -> list[str]:
        if not self._branch:
            return []
        token, _ = await self._tokens.installation_token()
        forgejo = self._client.client
        response = await forgejo.request(
            "GET",
            f"{self._repo}/compare/{quote(sha, safe='')}...{quote(self._branch, safe='')}",
            token=token,
        )
        if response.is_error:
            return []
        commits = forgejo._data(response).get("commits") or []
        return _first_parents_after(commits, sha)[:LOOK_AHEAD]

    # A batch's commit changes and failed-job logs are read on GitHub only:
    # on Forgejo a red batch is told to every line in it.

    async def commit_change(self, sha: str) -> CommitChange | None:
        return None

    async def failed_paths(self, head: str, *, tests_only: bool = False) -> list[str]:
        return []
