"""取一版学生代码，打成评测主机要的那个 tar（方案 §一次评测怎么跑）。

后端用这个项目仓库的**只读**凭据（``forge.tokens_for_project(...).read_token()``）
只取提交时记下的那一个 commit。评测主机上没有任何凭据，容器也不联网。

* 平台托管的仓库：``git fetch --depth 1 <sha>`` 再 ``git archive``，凭据放在请求头里
  （``project.starter.Remote.env``），不进地址、不落配置文件。
* 连了自己 GitHub 的项目：用项目那个 GitHub App 安装的只读令牌（只对这一个仓库、
  只读）向 GitHub 要这个 commit 的 tarball（``GET /repos/{repo}/tarball/{sha}``，它把人
  转到 codeload 的一次性地址，那一跳不带令牌），边下边数字节，再在后端把 GitHub 包外
  那一层 ``<owner>-<repo>-<sha>/`` 目录剥掉、重新打成和 ``git archive`` 一样的平铺 tar。
  这个 commit 在 GitHub 上已经找不到（仓库删了、提交被强推覆盖）：拒，文案
  ``judgeGitHubCommitGone``。
* 代码包超过 ``judge_code_max_bytes``（默认 8 MB）：拒，文案 ``judgeCodeTooLarge``；
  GitHub 的压缩包和解开后的文件各数一遍，解压炸弹到上限就停。
* 托管方这一刻没答上来（超时、仓库正忙、GitHub 配额用完）：那是平台的原因，抛
  ``GatewayUnavailableError``，由派发那一侧重试。
"""

import asyncio
import io
import logging
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import GatewayUnavailableError
from app.core.forge_http import forge_client
from app.core.sentences import NoticeText, say
from app.domain.project import forge
from app.domain.project.github_app import GitHubAppError
from app.domain.project.models import ProjectForge
from app.domain.project.starter import Remote

log = logging.getLogger(__name__)

#: Forgejo, the platform's own forge (``ProjectForge.kind``).
HOSTED = "forgejo"
#: A repository on GitHub, reached through the platform's GitHub App.
GITHUB = "github_app"
GIT_TIMEOUT_S = 300
#: How long one download of a GitHub tarball may take.
GITHUB_TIMEOUT_S = 120
_REDIRECTS = (301, 302, 303, 307, 308)


class CodeRefused(Exception):
    """This code cannot be judged, and trying again will not change that."""

    def __init__(self, message: NoticeText) -> None:
        super().__init__(str(message))
        self.message = message


async def remote_for(project_id: uuid.UUID, session: AsyncSession) -> Remote:
    """The project's repository as git reaches it, with a read-only token."""
    binding = await forge.binding_for_project(project_id, session)
    if binding is None:
        raise CodeRefused(say("judgeNoRepository"))
    if binding.kind != HOSTED:
        raise CodeRefused(say("judgeNoRepository"))
    tokens = await forge.tokens_for_project(project_id, session)
    if tokens is None:
        raise CodeRefused(say("judgeNoRepository"))
    token, _ = await tokens.read_token()
    return Remote(
        url=binding.api_url.removesuffix("/api/v1").rstrip("/")
        + f"/{binding.repo}.git",
        username=binding.repo.split("/", 1)[0],
        password=token,
    )


def _git(work: Path, *args: str, env: dict) -> None:
    result = subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if result.returncode:
        # stderr can carry the repository's address; it goes to the log only.
        log.warning("judge git %s failed: %s", args[0], result.stderr.strip())
        raise GatewayUnavailableError(say("judgeCodeUnreadable"))


def archive(remote: Remote, sha: str, max_bytes: int) -> bytes:
    """The tree of commit ``sha`` as a tar, fetched alone (depth 1)."""
    env = remote.env()
    with tempfile.TemporaryDirectory(prefix="judge-code-") as temporary:
        work = Path(temporary) / "code.git"
        _git(Path(temporary), "init", "-q", "--bare", str(work), env=env)
        _git(work, "fetch", "-q", "--depth", "1", remote.url, sha, env=env)
        process = subprocess.Popen(
            ["git", "-C", str(work), "archive", "--format=tar", sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        assert process.stdout is not None
        data = process.stdout.read(max_bytes + 1)
        process.stdout.close()
        if len(data) > max_bytes:
            process.kill()
        code = process.wait(timeout=GIT_TIMEOUT_S)
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if code:
        raise GatewayUnavailableError(say("judgeCodeUnreadable"))
    return data


def _too_large(max_bytes: int) -> CodeRefused:
    return CodeRefused(say("judgeCodeTooLarge", mb=max_bytes // (1024 * 1024)))


def flatten_github_tarball(data: bytes, max_bytes: int) -> bytes:
    """GitHub's gzipped tarball of one commit, as the flat tar ``git archive``
    gives: the ``<owner>-<repo>-<sha>/`` folder every entry sits in is dropped.
    Only folders, files and symbolic links are kept (the judge host's own
    extraction filters links again); the files may add up to ``max_bytes``."""
    out = io.BytesIO()
    total = 0
    try:
        with (
            tarfile.open(fileobj=io.BytesIO(data), mode="r|gz") as source,
            tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as target,
        ):
            for member in source:
                _top, _, rest = member.name.partition("/")
                rest = rest.strip("/")
                if not rest or not (
                    member.isdir() or member.isfile() or member.issym()
                ):
                    continue
                total += member.size if member.isfile() else 0
                if total > max_bytes:
                    raise _too_large(max_bytes)
                member.name = rest
                member.pax_headers = {}
                body = source.extractfile(member) if member.isfile() else None
                target.addfile(member, body)
    except (tarfile.TarError, EOFError, OSError) as exc:
        log.warning("judge github tarball unreadable: %s", exc)
        raise GatewayUnavailableError(say("judgeCodeUnreadable")) from exc
    if out.tell() > max_bytes:
        raise _too_large(max_bytes)
    return out.getvalue()


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


async def github_tarball(
    session: AsyncSession,
    project_id: uuid.UUID,
    binding: ProjectForge,
    sha: str,
    max_bytes: int,
) -> bytes:
    """The gzipped tarball of commit ``sha`` of the project's GitHub
    repository, read with a read-only token of the project's installation."""
    tokens = await forge.tokens_for_project(project_id, session)
    if tokens is None:
        raise CodeRefused(say("judgeNoRepository"))
    url = (
        f"{binding.api_url.rstrip('/')}/repos/{binding.repo}"
        f"/tarball/{quote(sha, safe='')}"
    )
    try:
        token, _ = await tokens.read_token()
        async with forge_client(
            transport=getattr(tokens, "transport", None), timeout=GITHUB_TIMEOUT_S
        ) as client:
            async with client.stream(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            ) as first:
                if first.status_code in _REDIRECTS and first.headers.get("location"):
                    # codeload's address carries its own one-time grant: the
                    # token does not travel to a second host.
                    async with client.stream(
                        "GET", first.headers["location"]
                    ) as second:
                        status = second.status_code
                        data = (
                            await _read_capped(second, max_bytes)
                            if status == 200
                            else b""
                        )
                else:
                    status = first.status_code
                    data = (
                        await _read_capped(first, max_bytes) if status == 200 else b""
                    )
    except (httpx.HTTPError, GitHubAppError) as exc:
        log.warning("judge github tarball failed: %s", exc)
        raise GatewayUnavailableError(say("judgeCodeUnreadable")) from exc
    if status in (404, 422):
        raise CodeRefused(say("judgeGitHubCommitGone"))
    if status != 200:
        log.warning("judge github tarball answered %s", status)
        raise GatewayUnavailableError(say("judgeCodeUnreadable"))
    return data


async def fetch_archive(
    session: AsyncSession, project_id: uuid.UUID, sha: str
) -> bytes:
    """Commit ``sha`` of the project's repository, as a tar of its files."""
    max_bytes = settings.judge_code_max_bytes
    binding = await forge.binding_for_project(project_id, session)
    if binding is not None and binding.kind == GITHUB:
        packed = await github_tarball(session, project_id, binding, sha, max_bytes)
        return await asyncio.to_thread(flatten_github_tarball, packed, max_bytes)
    remote = await remote_for(project_id, session)
    return await asyncio.to_thread(archive, remote, sha, max_bytes)
