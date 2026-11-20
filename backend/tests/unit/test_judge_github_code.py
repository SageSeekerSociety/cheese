"""连了自己 GitHub 的学生项目也能评测（方案 §第⑤步）。

提交时记下默认分支指着的那个 commit（``code_version``），评测时用项目那个 GitHub App
安装的只读令牌取这个 commit 的 tarball（``judge.code``）：GitHub 先把人转到 codeload
的一次性地址，那一跳不带令牌；包外那一层 ``<owner>-<repo>-<sha>/`` 剥掉，交给评测主机的
是和 ``git archive`` 一样的平铺 tar。GitHub 是一个 ``httpx.MockTransport``，绑定与令牌
是替身，别的都是真的。
"""

from __future__ import annotations

import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.core.errors import GatewayUnavailableError
from app.domain.judge import code
from app.domain.project import forge
from app.domain.task import code_version

SHA = "a" * 40
BINDING = SimpleNamespace(
    kind="github_app",
    api_url="https://api.github.test",
    repo="alice/two-sum",
    default_branch="trunk",
)


def _github_tarball(
    files: dict[str, bytes], top: str = f"alice-two-sum-{SHA[:7]}"
) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        folder = tarfile.TarInfo(top)
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)
        for name, body in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


def _names(flat: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(flat), mode="r:") as tar:
        return {
            m.name: tar.extractfile(m).read()  # type: ignore[union-attr]
            for m in tar.getmembers()
            if m.isfile()
        }


class _Tokens:
    def __init__(self, handler) -> None:
        self.transport = httpx.MockTransport(handler)
        self.asked: list[str] = []

    async def read_token(self) -> tuple[str, str]:
        self.asked.append("read")
        return "ghs_read_only", "2099-01-01T00:00:00Z"


@pytest.fixture
def github(monkeypatch):
    """GitHub answering the tarball of ``SHA``; ``seen`` records each request."""
    seen: list[httpx.Request] = []
    state = {
        "tarball": _github_tarball({"main.py": b"print(1)\n", "lib/a.py": b"x = 1\n"})
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.test":
            if request.url.path != f"/repos/alice/two-sum/tarball/{SHA}":
                return httpx.Response(404)
            return httpx.Response(
                302,
                headers={
                    "location": "https://codeload.github.test/alice/two-sum/legacy.tar.gz/x?token=once"
                },
            )
        return httpx.Response(200, content=state["tarball"])

    tokens = _Tokens(handler)

    async def binding_for_project(project_id, session):
        return BINDING

    async def tokens_for_project(project_id, session):
        return tokens

    monkeypatch.setattr(forge, "binding_for_project", binding_for_project)
    monkeypatch.setattr(forge, "tokens_for_project", tokens_for_project)
    return SimpleNamespace(seen=seen, tokens=tokens, state=state)


async def test_a_github_commit_is_fetched_read_only_and_flattened(github):
    flat = await code.fetch_archive(None, "project", SHA)  # type: ignore[arg-type]

    assert _names(flat) == {"main.py": b"print(1)\n", "lib/a.py": b"x = 1\n"}
    assert github.tokens.asked == ["read"]
    first, second = github.seen
    assert first.headers["authorization"] == "Bearer ghs_read_only"
    # The one-time codeload address is not handed the installation token.
    assert second.url.host == "codeload.github.test"
    assert "authorization" not in second.headers


async def test_a_commit_github_no_longer_has_is_refused_with_a_sentence(github):
    with pytest.raises(code.CodeRefused) as refused:
        await code.fetch_archive(None, "project", "b" * 40)  # type: ignore[arg-type]
    assert refused.value.message.key == "judgeGitHubCommitGone"


async def test_a_github_tarball_over_the_cap_is_refused(github, monkeypatch):
    monkeypatch.setattr(settings, "judge_code_max_bytes", 1024 * 1024)
    # Compresses to almost nothing, unpacks past the cap: counted unpacked too.
    github.state["tarball"] = _github_tarball({"big.txt": b"0" * (2 * 1024 * 1024)})
    with pytest.raises(code.CodeRefused) as refused:
        await code.fetch_archive(None, "project", SHA)  # type: ignore[arg-type]
    assert refused.value.message.key == "judgeCodeTooLarge"


async def test_a_github_answer_that_is_not_a_tarball_is_tried_again_later(github):
    github.state["tarball"] = b"<html>busy</html>"
    with pytest.raises(GatewayUnavailableError):
        await code.fetch_archive(None, "project", SHA)  # type: ignore[arg-type]


async def test_hand_in_records_the_default_branch_commit_of_a_github_project(
    monkeypatch,
):
    asked: list[str] = []

    async def binding_for_project(project_id, session):
        return BINDING

    async def branch_commit(project_id, session, branch):
        asked.append(branch)
        return SHA

    monkeypatch.setattr(forge, "binding_for_project", binding_for_project)
    monkeypatch.setattr(forge, "branch_commit", branch_commit)

    version = await code_version.for_project(None, "project")  # type: ignore[arg-type]

    assert version is not None and version.sha == SHA and version.branch == "trunk"
    assert asked == ["trunk"]
