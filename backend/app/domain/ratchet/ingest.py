"""One pull of the ratchet artifacts into the archive.

This is the whole of 「后端怎么拿」: list the artifacts of the configured
repository, skip the CI runs already stored, read the rest, store them. It is
idempotent — a pull that overlaps another stores nothing twice — so the periodic
job and the page's refresh button can both call it without coordinating.

The token is the platform App's, minted for the repo's own installation, i.e.
the same read-only identity a project's repository fetch uses. Nothing here
writes to GitHub.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent.github_app import github_app_tokens_for_project
from app.domain.project.repositories import ProjectGitInstallationRepository
from app.domain.ratchet.artifacts import Artifact, GitHubArtifacts, read_snapshot
from app.domain.ratchet.store import RatchetSnapshots

#: How many artifacts one pull looks at. The archive is filled newest-first, so
#: a first run against a repository with years of history still stores the
#: recent ones; raising this only matters for a backfill.
DEFAULT_ARTIFACT_LIMIT = 100


class ArtifactSource(Protocol):
    """What a pull needs from GitHub. A protocol so the pull can be tested
    without a token, a network, or an App installation."""

    async def list(
        self, *, owner: str, repo: str, token: str, limit: int
    ) -> list[Artifact]: ...

    async def download(
        self, *, owner: str, repo: str, artifact_id: int, token: str
    ) -> bytes: ...


@dataclass(frozen=True)
class Pulled:
    """One artifact, turned into the row it should become."""

    row: dict[str, Any]
    unreadable: bool
    failed: bool


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def row_for(artifact: Artifact, read, repo: str) -> Pulled:
    """The row for one downloaded artifact.

    A store-everything policy with an honest label, rather than a store-only-
    good-runs one: a CI run that failed to collect, and an artifact this code
    cannot read, both belong in the series as the holes they are. Storing only
    the good ones would draw a line whose every point is a pass and whose gaps
    are invisible.
    """
    base: dict[str, Any] = {
        "repo": repo,
        "workflow_run_id": artifact.run_id,
        "artifact_id": artifact.id,
        "run_url": artifact.run_url or None,
        "commit_sha": artifact.head_sha,
        "commit_date": None,
        "collected_at": artifact.created_at,
        "snapshot_version": read.version,
        "collection": "failed",
        "reason": read.reason,
        "payload": read.payload,
    }
    if read.payload is not None:
        payload = read.payload
        base["commit_sha"] = payload.get("commit") or artifact.head_sha
        base["commit_date"] = _parse_time(payload.get("commit_date"))
        base["collected_at"] = (
            _parse_time(payload.get("collected_at")) or artifact.created_at
        )
        # The collector's own word for it. Anything else in the document is a
        # shape this reader did not expect; keeping the payload and calling it
        # failed is the honest reading — the numbers may be there, but a reader
        # that trusts an unlabelled document is guessing.
        collection = payload.get("collection")
        base["collection"] = collection if collection in ("ok", "failed") else "failed"
        if base["collection"] == "failed":
            base["reason"] = str(payload.get("reason") or "采集失败，快照没有写明原因")
        base["run_url"] = str(payload.get("run_url") or artifact.run_url or "") or None
    return Pulled(
        row=base,
        unreadable=read.unreadable,
        failed=base["collection"] != "ok",
    )


async def ingest_snapshots(sessions: SessionFactory, **kwargs: Any) -> dict[str, Any]:
    """Open a session and pull — what the periodic job calls.

    The job registry holds a session factory, and a route holds a session; the
    pull itself wants neither in particular, so it takes a session and this is
    the one-liner that gives it one.
    """
    async with sessions() as session:
        return await ingest_once(session, **kwargs)


async def ingest_once(
    session: AsyncSession,
    *,
    source: ArtifactSource | None = None,
    repo: str | None = None,
    limit: int = DEFAULT_ARTIFACT_LIMIT,
) -> dict[str, Any]:
    """Pull the newest artifacts and store the ones not stored yet.

    Returns a report shaped for `PeriodicRunner`: counts, plus `error` when the
    pull could not happen at all (no App installation, GitHub refused). An
    all-zero report stays quiet in the log, which is the point — a pull that
    found nothing new every fifteen minutes is not worth a line.
    """
    repo = repo or settings.ratchet_repository
    report: dict[str, Any] = {
        "repo": repo,
        "listed": 0,
        "stored": 0,
        "already_stored": 0,
        "unreadable": 0,
        "failed": 0,
        "error": "",
    }
    if not repo:
        report["error"] = "没有配置棘轮仓库"
        return report
    owner, _, name = repo.partition("/")
    if not owner or not name:
        report["error"] = f"棘轮仓库名不是 owner/repo：{repo}"
        return report

    reader: ArtifactSource = source or GitHubArtifacts()
    store = RatchetSnapshots(session)

    installation = await ProjectGitInstallationRepository(session).get_by_repo(repo)
    if installation is None:
        report["error"] = f"{repo} 没有连接 GitHub App 安装"
        return report
    tokens = await github_app_tokens_for_project(installation.project_id, session)
    if tokens is None:
        report["error"] = "平台 App 没有配置，读不到 GitHub 工件"
        return report
    token, _expires = await tokens.installation_token()

    artifacts = await reader.list(owner=owner, repo=name, token=token, limit=limit)
    report["listed"] = len(artifacts)
    known = await store.known_run_ids(repo, [a.run_id for a in artifacts if a.run_id])

    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not artifact.run_id or artifact.run_id in known:
            report["already_stored"] += 1
            continue
        if artifact.expired:
            # An expired artifact cannot be downloaded, and a row saying
            # "expired" would put a point in the series that no check ever
            # produced. Counted, not stored: the archive is what was collected.
            report["unreadable"] += 1
            continue
        blob = await reader.download(
            owner=owner, repo=name, artifact_id=artifact.id, token=token
        )
        pulled = row_for(artifact, read_snapshot(blob), repo)
        rows.append(pulled.row)
        report["unreadable"] += int(pulled.unreadable)
        report["failed"] += int(pulled.failed)

    report["stored"] = await store.store_many(rows)
    return report
