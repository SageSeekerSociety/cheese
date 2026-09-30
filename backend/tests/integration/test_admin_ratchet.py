"""棘轮后台页：门、序列形状、以及「刷新」真的把没入库的工件存下来。

这一批要钉的：

* **门**。这是平台自己的债，不是某个项目的数；陌生登录用户读到的是 403。
* **页面只读库**。它读的是配置里那个仓库的行——另一个仓库的快照不会串进来。
* **刷新是幂等的**。同一次 CI run 再拉一遍存不进去第二行；新 run 存一行。
* **拉不动的时候不装成功**。没有 App 安装时 `refresh.error` 说明原因，页面照旧
  显示库里已有的点，不清空、也不假装拉过。

刷新那两条用一个假工件源（`ratchet_ingest.GitHubArtifacts` 的替身）和假的 App
令牌：真 GitHub 不该出现在测试里，而这条路径要测的正是「列出来 → 跳过已有的 →
读 → 入库」这四步的接线。
"""

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.project import forge as project_forge
from app.domain.project.models import Project, ProjectGitInstallation
from app.domain.ratchet import ingest as ratchet_ingest
from app.domain.ratchet.artifacts import Artifact, RatchetGitHubError
from app.domain.ratchet.models import RatchetSnapshot
from tests.conftest import seed_user
from tests.integration.conftest import a_team, session_auth_headers

ADMIN = "ratchet-admin"
STRANGER = "ratchet-stranger"
REPO = "owner/ratchet-test"
BASE = datetime(2026, 9, 20, 4, 23, tzinfo=UTC)


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    monkeypatch.setattr(settings, "ratchet_repository", REPO)
    return ADMIN


def _payload(commit: str, *, actual: int, day: int, fingerprint: str = "fp1") -> dict:
    return {
        "version": 1,
        "collection": "ok",
        "commit": commit,
        "commit_date": (BASE + timedelta(days=day)).isoformat(),
        "collected_at": (BASE + timedelta(days=day, hours=1)).isoformat(),
        "run_url": f"https://example.invalid/runs/{commit[:4]}",
        "checks": [
            {
                "id": "be-contracts",
                "area": "边界",
                "status": "pass",
                "actual": actual,
                "frozen": 54,
                "better": "down",
                "rule_fingerprint": fingerprint,
            }
        ],
        "board": None,
    }


def _artifact_zip(payload: dict) -> bytes:
    """The bytes GitHub hands back: a zip whose member is the snapshot."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ratchet-snapshot.json", json.dumps(payload))
    return buffer.getvalue()


def _seed(client, rows: list[dict]) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            for spec in rows:
                session.add(RatchetSnapshot(**spec))
            await session.commit()

    asyncio.run(_run())


def _row(
    commit: str, *, day: int, run_id: int, repo: str = REPO, actual: int = 5
) -> dict:
    payload = _payload(commit, actual=actual, day=day)
    return {
        "repo": repo,
        "workflow_run_id": run_id,
        "commit_sha": commit,
        "commit_date": BASE + timedelta(days=day),
        "collected_at": BASE + timedelta(days=day, hours=1),
        "snapshot_version": 1,
        "collection": "ok",
        "reason": None,
        "payload": payload,
        "run_url": payload["run_url"],
        "artifact_id": 1000 + run_id,
        "ingested_at": BASE + timedelta(days=day, hours=2),
    }


class _Source:
    """Stands in for GitHubArtifacts: two artifacts, one of them new."""

    def __init__(self, blobs: dict[int, bytes]) -> None:
        self._blobs = blobs
        self.downloaded: list[int] = []

    async def list(
        self, *, owner: str, repo: str, token: str, limit: int
    ) -> list[Artifact]:
        assert (owner, repo) == ("owner", "ratchet-test")
        return [
            Artifact(
                id=artifact_id,
                run_id=run_id,
                head_sha=f"{run_id:040d}",
                created_at=BASE + timedelta(days=run_id),
                run_url=f"https://example.invalid/runs/{run_id}",
                expired=False,
            )
            for run_id, artifact_id in ((22, 2), (21, 1))
        ]

    async def download(
        self, *, owner: str, repo: str, artifact_id: int, token: str
    ) -> bytes:
        self.downloaded.append(artifact_id)
        return self._blobs[artifact_id]


class _Tokens:
    def __init__(self) -> None:
        self.calls = 0

    async def installation_token(self) -> tuple[str, str]:
        self.calls += 1
        return "app-token", "2026-09-30T12:00:00+00:00"


async def _fake_tokens_for_project(project_id, session) -> _Tokens:
    return _Tokens()


def _fake_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """凭据那一跳换成假的：真 GitHub 不该出现在测试里。

    换的是 `project.forge` 里的那一句 —— 采集自己是按仓库名问 forge 要凭据的，
    所以「哪个仓库归哪个项目」这一段走的是真查询。
    """
    monkeypatch.setattr(
        project_forge, "github_app_tokens_for_project", _fake_tokens_for_project
    )


def _connect_repo(client) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            project = Project(
                team_id=await a_team(session), name="P", owner_handle=ADMIN
            )
            session.add(project)
            await session.flush()
            session.add(
                ProjectGitInstallation(
                    project_id=project.id,
                    installation_id=77,
                    repo=REPO,
                    account="owner",
                )
            )
            await session.commit()

    asyncio.run(_run())


def test_a_stranger_cannot_read_the_board(client, as_admin):
    seed_user(client, STRANGER)

    response = client.get("/admin/ratchet", headers=session_auth_headers(STRANGER))

    assert response.status_code == 403


def test_the_board_is_read_from_the_stored_snapshots(client, as_admin):
    seed_user(client, ADMIN)
    _seed(
        client,
        [
            _row("a" * 40, day=1, run_id=11, actual=6),
            _row("b" * 40, day=2, run_id=12, actual=4),
        ],
    )

    body = client.get("/admin/ratchet", headers=session_auth_headers(ADMIN)).json()[
        "data"
    ]

    assert body["collected_commit"] == "b" * 40
    assert body["deployed_commit"] == settings.app_version
    check = body["areas"][0]["checks"][0]
    assert check["id"] == "be-contracts"
    assert check["direction"] == "improving"
    assert [point["actual"] for point in check["points"]] == [6, 4]
    assert body["points"] == 2


def test_another_repositorys_snapshots_do_not_leak_in(client, as_admin):
    seed_user(client, ADMIN)
    _seed(client, [_row("c" * 40, day=3, run_id=13, repo="someone/else")])

    body = client.get("/admin/ratchet", headers=session_auth_headers(ADMIN)).json()[
        "data"
    ]

    assert body["areas"] == []
    assert body["total_stored"] == 0


def test_refresh_stores_only_the_runs_that_are_not_stored_yet(
    client, as_admin, monkeypatch: pytest.MonkeyPatch
):
    seed_user(client, ADMIN)
    _connect_repo(client)
    _seed(client, [_row("a" * 40, day=1, run_id=21, actual=6)])
    source = _Source(
        {
            1: _artifact_zip(_payload("a" * 40, actual=6, day=1)),
            2: _artifact_zip(_payload("d" * 40, actual=3, day=2)),
        }
    )
    monkeypatch.setattr(ratchet_ingest, "GitHubArtifacts", lambda: source)
    _fake_tokens(monkeypatch)

    body = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]

    assert body["refresh"]["listed"] == 2
    assert body["refresh"]["already_stored"] == 1
    assert body["refresh"]["stored"] == 1
    assert body["refresh"]["error"] == ""
    # The stored run was never downloaded a second time: a re-poll is a listing
    # call, which is what makes the button safe to press.
    assert source.downloaded == [2]
    assert body["collected_commit"] == "d" * 40

    # Pressed again, both runs are known: the second press lists and stops.
    again = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]
    assert again["refresh"]["listed"] == 2
    assert again["refresh"]["stored"] == 0
    assert again["refresh"]["already_stored"] == 2
    assert source.downloaded == [2]
    assert again["total_stored"] == 2


def test_a_refresh_that_cannot_pull_says_so_and_keeps_the_board(
    client, as_admin, monkeypatch: pytest.MonkeyPatch
):
    seed_user(client, ADMIN)
    # Its own repository, so 「没有安装」 is a fact about this test and not about
    # whether the installation another test connected is still in the database.
    monkeypatch.setattr(settings, "ratchet_repository", "owner/not-connected")
    _seed(
        client, [_row("a" * 40, day=1, run_id=21, actual=6, repo="owner/not-connected")]
    )

    class _Recorder:
        listed = False

        async def list(self, **kwargs):
            self.listed = True
            return []

        async def download(self, **kwargs):
            raise AssertionError("nothing should be downloaded without an installation")

    recorder = _Recorder()
    monkeypatch.setattr(ratchet_ingest, "GitHubArtifacts", lambda: recorder)

    body = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]

    assert "GitHub App 安装" in body["refresh"]["error"]
    assert body["refresh"]["stored"] == 0
    # The pull stops at the missing installation — it does not go on to list
    # artifacts it has no token for.
    assert recorder.listed is False
    # The board is still the stored one — a failed pull adds nothing and takes
    # nothing away.
    assert body["points"] == 1
    assert body["collected_commit"] == "a" * 40


def test_a_refresh_that_github_refuses_reports_it_instead_of_raising(
    client, as_admin, monkeypatch: pytest.MonkeyPatch
):
    """GitHub 拒绝时，POST 仍然是 200，原因原话在 `refresh.error` 里。

    这条路径上抛异常的话，路由只会回一个 500，而「谁拒绝了、为什么」正是要给人看的
    那句 —— 也是这一页唯一能解释「为什么没有新点」的地方。
    """
    seed_user(client, ADMIN)
    _connect_repo(client)

    class _Refusing:
        async def list(self, **kwargs):
            raise RatchetGitHubError("GitHub 拒绝列出棘轮工件（HTTP 403）：rate limit")

        async def download(self, **kwargs):
            raise AssertionError("列都列不出来，不该走到下载")

    monkeypatch.setattr(ratchet_ingest, "GitHubArtifacts", _Refusing)
    _fake_tokens(monkeypatch)

    response = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    )

    assert response.status_code == 200
    refresh = response.json()["data"]["refresh"]
    assert "rate limit" in refresh["error"]
    assert refresh["stored"] == 0


def test_an_artifact_this_reader_cannot_read_is_retried_not_archived(
    client, as_admin, monkeypatch: pytest.MonkeyPatch
):
    """读不懂的工件不入归档，也不占住那次 run：下一次拉还会再拿到它。

    反过来说：`ratchet-snapshot.failed.txt` 那种「采集自己说这次没采到」是另一回事，
    它是一条永久的洞，见下一条。
    """
    seed_user(client, ADMIN)
    _connect_repo(client)
    source = _Source(
        {
            1: _artifact_zip({"version": 99, "checks": []}),
            2: _artifact_zip(_payload("d" * 40, actual=3, day=2)),
        }
    )
    monkeypatch.setattr(ratchet_ingest, "GitHubArtifacts", lambda: source)
    _fake_tokens(monkeypatch)

    body = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]

    assert body["refresh"]["stored"] == 1
    assert body["refresh"]["unreadable"] == 1
    # 只有读得懂的那一次进了归档。
    assert body["points"] == 1
    assert body["collected_commit"] == "d" * 40
    assert source.downloaded == [2, 1]

    # 再按一次：存过的那次跳过，读不懂的那次**再拉一次** —— 这就是以后能补回来的
    # 那个机制；存成一行 failed 的话它再也回不来。
    again = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]
    assert again["refresh"]["stored"] == 0
    assert again["refresh"]["already_stored"] == 1
    assert again["refresh"]["unreadable"] == 1
    assert source.downloaded == [2, 1, 1]


def test_a_failed_collection_marker_is_archived_as_a_hole(
    client, as_admin, monkeypatch: pytest.MonkeyPatch
):
    """采集自己说没采到 → 存成一条洞，页面上是「没跑到」，不是没有这个点。"""
    seed_user(client, ADMIN)
    _connect_repo(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ratchet-snapshot.failed.txt", "no interpreter (exit 127)")
    source = _Source(
        {1: buffer.getvalue(), 2: _artifact_zip(_payload("d" * 40, actual=3, day=2))}
    )
    monkeypatch.setattr(ratchet_ingest, "GitHubArtifacts", lambda: source)
    _fake_tokens(monkeypatch)

    body = client.post(
        "/admin/ratchet/refresh", headers=session_auth_headers(ADMIN)
    ).json()["data"]

    assert body["refresh"]["stored"] == 2
    assert body["refresh"]["unreadable"] == 0
    failed = [point for point in body["collections"] if point["collection"] == "failed"]
    assert len(failed) == 1
    assert "no interpreter" in failed[0]["reason"]
