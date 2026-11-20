"""A reader's browser reports how long a project page took, request by request;
a platform admin reads the reports back. Nobody else reads them, and a
stranger to the project cannot write one."""

from app.core.config import settings
from tests.integration.conftest import post_project, session_auth_headers

TIMING = {
    "route": "/projects/:id/overview",
    "host": "okcheese.com",
    "first_load": True,
    "loaded": 1800,
    "requests": [
        {
            "path": "/projects/:id/progress",
            "start": 900,
            "duration": 640,
            "ttfb": 600,
            "bytes": 13000,
        }
    ],
}


def test_a_member_reports_and_an_admin_reads_it_back(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["timing-admin"])
    project = post_project(
        client, json={"name": "Timed"}, headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    sent = client.post(
        f"/projects/{project}/page-timings",
        json=TIMING,
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 200, sent.text

    stranger = client.post(
        f"/projects/{project}/page-timings",
        json=TIMING,
        headers=session_auth_headers("timing-stranger"),
    )
    assert stranger.status_code in (403, 404), stranger.text
    assert (
        client.get(
            "/admin/page-timings", headers=session_auth_headers("alice")
        ).status_code
        == 403
    )

    read = client.get(
        "/admin/page-timings",
        params={"route": "/projects/:id/overview"},
        headers=session_auth_headers("timing-admin"),
    )
    assert read.status_code == 200, read.text
    mine = [row for row in read.json()["data"] if row["handle"] == "alice"]
    assert mine and mine[0]["requests"][0]["duration"] == 640
