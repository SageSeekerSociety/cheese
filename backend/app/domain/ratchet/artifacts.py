"""Reading `ratchet-snapshot` artifacts out of GitHub Actions.

GitHub is the transport, not the archive (see the package docstring): this
module knows how to list the artifacts of one repository and how to turn one of
them into a snapshot document, and nothing about where the document goes.

Two things it refuses to do, both for the same reason — the page must never
show a number that was not measured:

* It does not treat a missing snapshot as an empty one. An artifact holding only
  ``ratchet-snapshot.failed.txt`` is read as a FAILED COLLECTION with that file's
  text as the reason, which is what the collector wrote it for.
* It does not read a format it does not know. ``SUPPORTED_VERSIONS`` is a
  whitelist; a payload outside it is returned as unreadable, counted, and left
  out of the archive. Guessing at an unknown shape is how a page starts drawing
  zeros for checks that were never understood.
"""

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

#: Snapshot shapes this reader understands. `ratchet-snapshot.py`'s
#: SNAPSHOT_VERSION is the other side of this pair; the version exists so that a
#: shape change is a refusal here, not a misread number on the page.
SUPPORTED_VERSIONS = frozenset({1})

_ARTIFACT_NAME = "ratchet-snapshot"
_SNAPSHOT_MEMBER = "ratchet-snapshot.json"
_FAILED_MEMBER = "ratchet-snapshot.failed.txt"
#: A snapshot of a full run is tens of KB; the extra room is for a bigger board
#: section later. The cap is here because a download is unbounded otherwise.
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

#: What one member may expand to. The cap above bounds the zip we download, which
#: bounds nothing inside it: a 5 KB artifact can declare a member of gigabytes,
#: and `ZipFile.read` would build that in memory without asking. Two guards, not
#: one — the declared size first (cheap), then a bounded read (a header can lie).
_MAX_MEMBER_BYTES = 8 * 1024 * 1024


class RatchetGitHubError(RuntimeError):
    """GitHub refused, or answered with something that is not an artifact."""


@dataclass(frozen=True)
class Artifact:
    """One CI run's uploaded artifact, as the listing describes it."""

    id: int
    run_id: int
    head_sha: str
    created_at: datetime | None
    run_url: str
    expired: bool


@dataclass(frozen=True)
class SnapshotRead:
    """What one artifact turned out to be.

    Exactly one of ``payload`` / ``reason`` is set when ``version`` is not None:
    the collector's document, or why there is none. ``unreadable`` covers an
    unknown version and a zip that is not a snapshot at all.
    """

    version: int | None
    payload: dict[str, Any] | None
    reason: str | None
    unreadable: bool = False


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubArtifacts:
    """The two REST calls this domain needs, against api.github.com."""

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list(
        self, *, owner: str, repo: str, token: str, limit: int
    ) -> list[Artifact]:
        """The newest ``limit`` artifacts named `ratchet-snapshot`, newest first.

        Repo-level rather than per-run: the run id of the last merge is not
        something the platform knows (a merge it did not perform has no local
        record), and this listing carries each artifact's run and commit, which
        is all the archive needs.
        """
        out: list[Artifact] = []
        page = 1
        while len(out) < limit:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=30.0
            ) as client:
                resp = await client.get(
                    f"{self._api_base}/repos/{owner}/{repo}/actions/artifacts",
                    headers=self._headers(token),
                    params={
                        "name": _ARTIFACT_NAME,
                        "per_page": min(100, max(1, limit - len(out))),
                        "page": page,
                    },
                )
            if resp.status_code != 200:
                # i18n-exempt: internal ratchet report, not shown in the product
                raise RatchetGitHubError(
                    f"GitHub 拒绝列出棘轮工件（HTTP {resp.status_code}）："
                    f"{resp.text[:300]}"
                )
            artifacts = resp.json().get("artifacts") or []
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                run = item.get("workflow_run") or {}
                head_sha = run.get("head_sha") if isinstance(run, dict) else None
                artifact_id = item.get("id")
                if not isinstance(artifact_id, int) or not isinstance(head_sha, str):
                    continue
                run_id = run.get("id") if isinstance(run, dict) else None
                out.append(
                    Artifact(
                        id=artifact_id,
                        run_id=run_id if isinstance(run_id, int) else 0,
                        head_sha=head_sha,
                        created_at=_parse_time(item.get("created_at")),
                        run_url=str(run.get("html_url") or "")
                        if isinstance(run, dict)
                        else "",
                        expired=bool(item.get("expired")),
                    )
                )
            if len(artifacts) < 100:
                break
            page += 1
        return out[:limit]

    async def download(
        self, *, owner: str, repo: str, artifact_id: int, token: str
    ) -> bytes:
        """Fetch an artifact's zip, stopping as soon as it passes the cap.

        Two requests because the API answers with a 302 to a signed URL that
        expires in a minute. The redirect is followed by hand so the token is
        not resent to the storage host: it is not ours, and the App's token is
        not what that URL authenticates with.

        Streamed, not buffered: `client.get` returns only once the whole body is
        in memory, so a length check after it measures something already eaten.
        Here the read stops mid-body — `_read_capped` never holds more than one
        chunk past the cap.
        """
        url = (
            f"{self._api_base}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip"
        )
        location: str | None = None
        async with httpx.AsyncClient(transport=self._transport, timeout=60.0) as client:
            async with client.stream(
                "GET",
                url,
                headers=self._headers(token),
                follow_redirects=False,
            ) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    # The signed URL, not the artifact: this body is dropped with
                    # the response and the download is taken from the redirect
                    # target below.
                    location = resp.headers.get("location")
                    if not location:
                        # i18n-exempt: internal ratchet report, not shown in the product
                        raise RatchetGitHubError("GitHub 的工件下载重定向没有 Location")
                else:
                    return await _read_capped(resp, artifact_id)
            async with client.stream("GET", location, follow_redirects=True) as resp:
                return await _read_capped(resp, artifact_id)


async def _read_capped(resp: httpx.Response, artifact_id: int) -> bytes:
    """The body of a streaming response, refusing one that grows past the cap.

    The check is on the running total, and it aborts the moment a chunk pushes
    it over: the response is not read to the end first. That is the difference
    between a cap on what the platform agrees to hold and a cap it applies after
    holding everything.
    """
    if resp.status_code != 200:
        # i18n-exempt: internal ratchet report, not shown in the product
        raise RatchetGitHubError(
            f"GitHub 拒绝下载棘轮工件 {artifact_id}（HTTP {resp.status_code}）"
        )
    total = 0
    chunks: list[bytes] = []
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > _MAX_ARTIFACT_BYTES:
            # i18n-exempt: internal ratchet report, not shown in the product
            raise RatchetGitHubError(
                f"棘轮工件 {artifact_id} 超过 {_MAX_ARTIFACT_BYTES} 字节，不下载"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    """Read one member, refusing anything that expands past the cap.

    None means "too big", which the caller turns into an unreadable artifact —
    the same reading as an unknown version, and for the same reason: this reader
    did not understand what it was given, so it stores nothing.
    """
    info = archive.getinfo(name)
    if info.file_size > _MAX_MEMBER_BYTES:
        return None
    with archive.open(info) as member:
        raw = member.read(_MAX_MEMBER_BYTES + 1)
    if len(raw) > _MAX_MEMBER_BYTES:
        return None
    return raw


def read_snapshot(blob: bytes) -> SnapshotRead:
    """Turn one downloaded artifact into a snapshot, or say why it is not one.

    Exhaustive by construction: every path returns a reason when there is no
    payload, so a caller can always store a row explaining a missing point.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = archive.namelist()
            if _SNAPSHOT_MEMBER in names:
                raw = _read_member(archive, _SNAPSHOT_MEMBER)
                if raw is None:
                    return SnapshotRead(
                        None,
                        None,
                        f"{_SNAPSHOT_MEMBER} 解压后超过 {_MAX_MEMBER_BYTES} 字节，不读",
                        unreadable=True,
                    )
            elif _FAILED_MEMBER in names:
                note = _read_member(archive, _FAILED_MEMBER)
                if note is None:
                    return SnapshotRead(
                        None,
                        None,
                        f"{_FAILED_MEMBER} 解压后超过 {_MAX_MEMBER_BYTES} 字节，不读",
                        unreadable=True,
                    )
                text = note.decode("utf-8", "replace").strip()
                return SnapshotRead(
                    None, None, text[:2000] or "采集失败，工件没有写明原因"
                )
            else:
                return SnapshotRead(
                    None, None, f"工件里没有 {_SNAPSHOT_MEMBER}", unreadable=True
                )
    except (zipfile.BadZipFile, OSError) as exc:
        return SnapshotRead(None, None, f"工件不是可读的 zip：{exc}", unreadable=True)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return SnapshotRead(None, None, f"快照不是合法 JSON：{exc}", unreadable=True)
    if not isinstance(payload, dict):
        return SnapshotRead(None, None, "快照的顶层不是对象", unreadable=True)
    version = payload.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in SUPPORTED_VERSIONS
    ):
        return SnapshotRead(
            None,
            None,
            f"快照版本 {version!r} 这版读不了（支持 {sorted(SUPPORTED_VERSIONS)}）",
            True,
        )
    commit = payload.get("commit")
    if not isinstance(commit, str) or not commit:
        return SnapshotRead(None, None, "快照没有记录提交", unreadable=True)
    return SnapshotRead(version, payload, None)
