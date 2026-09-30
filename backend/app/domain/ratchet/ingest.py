"""One pull of the ratchet artifacts into the archive.

This is the whole of 「后端怎么拿」: list the artifacts of the configured
repository, skip the CI runs already stored, read the rest, store them. It is
idempotent — a pull that overlaps another stores nothing twice — so the periodic
job and the page's refresh button can both call it without coordinating.

The token is the platform App's, minted for the repo's own installation, i.e.
the same read-only identity a project's repository fetch uses. Nothing here
writes to GitHub.

**A pull that could not happen reports instead of raising.** The callers are a
periodic job and the page's refresh button; neither has anywhere to put a
traceback, and a 500 would lose the one thing worth reading — GitHub's own
sentence about why. So GitHub's failures come back in `report["error"]`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.project.forge import github_tokens_for_repo
from app.domain.ratchet.artifacts import (
    Artifact,
    GitHubArtifacts,
    RatchetGitHubError,
    read_snapshot,
)
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
    """One artifact, turned into the row it should become.

    ``row`` is None for an artifact this reader cannot read at all (unknown
    version, not a zip). Nothing is stored for those, and that is deliberate:
    see `row_for`.
    """

    row: dict[str, Any] | None
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
    """The row for one downloaded artifact, or None when nothing should be stored.

    Two unreadable things are not the same thing, and the difference is what
    makes re-reading possible later:

    * **The collector said the run failed to collect** (`ratchet-snapshot.failed
      .txt`, or a payload whose own `collection` is not ``ok``). That is a hole
      in the series and it is permanent — the same artifact will say the same
      thing forever. It is stored, labelled, and drawn as the hole it is.
    * **This reader cannot read it** (unknown version, not a zip, no snapshot
      member). The run may have collected perfectly; what failed is our reading
      of it. Storing that as a failed collection would put a number nobody
      measured into the archive — and, because a stored run is never pulled
      again, would freeze the mistake: a later version of this reader could
      never come back for it. So it is counted as unreadable and left out.

    Returning None for the second case is what keeps the retry possible:
    `known_run_ids` only knows stored runs, so an unreadable artifact is offered
    again on the next pull.
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
    if read.payload is None and read.unreadable:
        return Pulled(row=None, unreadable=True, failed=False)
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

    # 凭据按**仓库名**问 project 那一块要（`project.forge` 正是「哪个仓库、什么凭据」
    # 的边界），采集自己不摸别的领域的 repository。
    tokens = await github_tokens_for_repo(repo, session)
    if tokens is None:
        report["error"] = (
            f"{repo} 没有可用的 GitHub App 安装（没连接，或平台 App 未配置）"
        )
        return report
    token, _expires = await tokens.installation_token()

    # GitHub 那侧的失败**不走异常**：这一趟拉不到，页面上已经存下的点还是真的，所以把
    # 原因原话写进 report，让它出现在 `board.refresh.error` 里。抛出去的话路由只回一个
    # 500，原因就没了 —— 而「谁拒绝了、为什么」正是要给人看的那句。
    try:
        artifacts = await reader.list(owner=owner, repo=name, token=token, limit=limit)
    except (RatchetGitHubError, httpx.HTTPError) as exc:
        report["error"] = f"列工件失败：{exc}"
        return report
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
        try:
            blob = await reader.download(
                owner=owner, repo=name, artifact_id=artifact.id, token=token
            )
        except (RatchetGitHubError, httpx.HTTPError):
            # One artifact of many: the rest of the pull is still worth doing, and
            # this run stays unstored so the next pull offers it again.
            report["unreadable"] += 1
            continue
        pulled = row_for(artifact, read_snapshot(blob), repo)
        if pulled.row is not None:
            rows.append(pulled.row)
        report["unreadable"] += int(pulled.unreadable)
        report["failed"] += int(pulled.failed)

    report["stored"] = await store.store_many(rows)
    return report
