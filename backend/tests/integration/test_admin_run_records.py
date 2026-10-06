"""The admin page's run records: the platform's errors and what it handled itself,
one line per kind of thing, for the platform's admins only."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import post_project, session_auth_headers


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["ops"])
    return session_auth_headers("ops")


def _project(client) -> uuid.UUID:
    return uuid.UUID(post_project(client, json={"name": "P"}).json()["data"]["id"])


def _keep(client, rows) -> None:
    async def add() -> None:
        async with client.test_factory() as s:
            s.add_all(rows)
            await s.commit()

    client.portal.call(add)


def _error(project, fingerprint, *, ago=timedelta(minutes=5)) -> RunRecord:
    return RunRecord(
        project_id=project,
        kind="backend_error",
        severity="error",
        content="后端报错（GET /projects/x/site）：ConnectError",
        meta={"fingerprint": fingerprint, "stack": "Traceback…\nConnectError"},
        created_at=datetime.now(UTC) - ago,
    )


def test_only_a_platform_admin_reads_them(client, admin):
    assert client.get("/admin/run-records", headers=admin).status_code == 200
    stranger = session_auth_headers("someone")
    assert client.get("/admin/run-records", headers=stranger).status_code == 403


def test_one_error_across_projects_is_one_line_that_counts_them(client, admin):
    a, b = _project(client), _project(client)
    _keep(
        client,
        [
            _error(a, "fp-site"),
            _error(a, "fp-site"),
            _error(b, "fp-site"),
            _error(a, "fp-other"),
            # Older than the window asked for: not counted.
            _error(a, "fp-site", ago=timedelta(days=2)),
            RunRecord(
                project_id=a,
                kind="api_retry",
                severity="warn",
                content="AI 服务请求失败，正在重试（第 3/10 次）",
                created_at=datetime.now(UTC),
            ),
        ],
    )

    data = client.get("/admin/run-records?window=24h", headers=admin).json()["data"]
    site = next(g for g in data["groups"] if g["key"] == "fp-site")
    assert (site["count"], site["projects"]) == (3, 2)
    assert sum(site["buckets"]) == 3
    assert data["totals"]["error_kinds"] == 2
    assert data["totals"]["recovered"] == 1

    only_errors = client.get("/admin/run-records?group=errors", headers=admin).json()[
        "data"
    ]["groups"]
    assert {g["kind"] for g in only_errors} == {"backend_error"}

    shown = client.get(
        "/admin/run-records/detail",
        params={"key": "fp-site", "kind": "backend_error"},
        headers=admin,
    ).json()["data"]
    assert "ConnectError" in shown["meta"]["stack"]
    assert {p["project_id"] for p in shown["places"]} == {str(a), str(b)}
