"""取一版学生代码，打成评测主机要的那个 tar（方案 §一次评测怎么跑）。

后端用这个项目仓库的**只读**凭据（``forge.tokens_for_project(...).read_token()``）
只取提交时记下的那一个 commit：``git fetch --depth 1 <sha>`` 再 ``git archive``，凭据
放在请求头里（``project.starter.Remote.env``），不进地址、不落配置文件。评测主机上没有
任何凭据，容器也不联网。

* 连了 GitHub 的项目这一步不评（方案 §分步计划第 3 步）：拒，文案 ``judgeGitHubLater``；
* 代码包超过 ``judge_code_max_bytes``（默认 8 MB）：拒，文案 ``judgeCodeTooLarge``；
* 托管方这一刻没答上来（超时、仓库正忙）：那是平台的原因，抛
  ``GatewayUnavailableError``，由派发那一侧重试。
"""

import asyncio
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import GatewayUnavailableError
from app.core.sentences import NoticeText, say
from app.domain.project import forge
from app.domain.project.starter import Remote

log = logging.getLogger(__name__)

#: Forgejo, the platform's own forge (``ProjectForge.kind``).
HOSTED = "forgejo"
GIT_TIMEOUT_S = 300


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
        raise CodeRefused(say("judgeGitHubLater"))
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
        raise CodeRefused(say("judgeCodeTooLarge", mb=max_bytes // (1024 * 1024)))
    if code:
        raise GatewayUnavailableError(say("judgeCodeUnreadable"))
    return data


async def fetch_archive(
    session: AsyncSession, project_id: uuid.UUID, sha: str
) -> bytes:
    """Commit ``sha`` of the project's repository, as a tar of its files."""
    remote = await remote_for(project_id, session)
    return await asyncio.to_thread(archive, remote, sha, settings.judge_code_max_bytes)
