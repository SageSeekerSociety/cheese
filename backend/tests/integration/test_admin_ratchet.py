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
from app.domain.project.models import Project, ProjectGitInstallation
from app.domain.ratchet import ingest as ratchet_ingest
from app.domain.ratchet.artifacts import Artifact
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
    monkeypatch.setattr(
        ratchet_ingest, "github_app_tokens_for_project", _fake_tokens_for_project
    )

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

    assert "没有连接 GitHub App 安装" in body["refresh"]["error"]
    assert body["refresh"]["stored"] == 0
    # The pull stops at the missing installation — it does not go on to list
    # artifacts it has no token for.
    assert recorder.listed is False
    # The board is still the stored one — a failed pull adds nothing and takes
    # nothing away.
    assert body["points"] == 1
    assert body["collected_commit"] == "a" * 40
