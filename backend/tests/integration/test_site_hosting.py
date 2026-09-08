"""A published website gets its own origin and only its viewer's Site access."""

import uuid

import pytest

from app.core.config import settings
from app.core.tokens import verify_session_token
from app.domain.site.hosting import content_origin, mint_site_token
from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers, session_token
from tests.machine_work import machine_commits


@pytest.fixture
def published(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "sites_port", None)
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")
    auth = session_auth_headers("alice")
    result = client.post(
        "/projects", json={"name": "Website", "owner_handle": "alice"}, headers=auth
    )
    assert result.status_code == 200, result.text
    project = uuid.UUID(result.json()["data"]["id"])
    topic = uuid.uuid4()
    machine_commits(
        project,
        topic,
        {
            "web/index.html": (
                '<html><script type="module" src="/app.js"></script>published</html>'
            ),
            "web/app.js": 'document.body.dataset.ready = "yes";',
            "web/guide/index.html": '<link rel="stylesheet" href="style.css">Guide',
            "web/guide/style.css": "body { color: blue }",
        },
    )
    assert ws.merge_topic(
        project, topic, message="feat: publish test site\n\nRequested-by: alice"
    )["merged"]
    source = client.get(f"/projects/{project}/site", headers=auth).json()["data"]
    response = client.post(
        f"/projects/{project}/site",
        headers=auth,
        json={
            "directory": "web",
            "expected_source_revision": source["source_revision"],
        },
    )
    assert response.status_code == 200, response.text
    return project, auth


def _open(client, project, auth):
    response = client.post(f"/projects/{project}/site-session", headers=auth)
    assert response.status_code == 200, response.text
    grant = response.json()["data"]
    exchanged = client.post(
        grant["url"],
        data={"grant": grant["grant"]},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert exchanged.status_code == 303, exchanged.text
    return grant, exchanged


def test_site_cookie_loads_modules_without_platform_credentials(client, published):
    project, auth = published
    grant, exchange = _open(client, project, auth)
    assert exchange.headers["location"] == "/"
    cookie = exchange.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Domain=" not in cookie
    assert session_token("alice") not in cookie
    origin = content_origin(project)
    page = client.get(origin + "/")
    assert page.status_code == 200 and "published" in page.text
    assert "allow-same-origin" in page.headers["content-security-policy"]
    assert "worker-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get(origin + "/app.js").text == 'document.body.dataset.ready = "yes";'
    assert (
        client.get(origin + "/app.js", headers={"Service-Worker": "script"}).status_code
        == 403
    )
    assert client.get(origin + "/health").status_code == 404
    assert (
        client.post(origin + "/projects", json={"name": "intrusion"}).status_code == 405
    )
    assert verify_session_token(grant["grant"]) is None
    assert (
        client.get(
            f"/projects/{project}/site",
            headers={"Authorization": "Bearer " + grant["grant"]},
        ).status_code
        == 401
    )


def test_content_host_rejects_platform_tokens_wrong_projects_and_expired_grants(
    client, published
):
    project, auth = published
    origin = content_origin(project)
    assert client.get(origin + "/", headers=auth).status_code == 401
    grant = client.post(f"/projects/{project}/site-session", headers=auth).json()[
        "data"
    ]["grant"]
    wrong_origin = content_origin(uuid.uuid4())
    assert (
        client.post(
            wrong_origin + "/_cheese/session",
            data={"grant": grant},
            headers={"Origin": settings.frontend_url},
        ).status_code
        == 401
    )
    expired = mint_site_token(project, "alice", purpose="site-grant", ttl=-1)
    assert (
        client.post(
            origin + "/_cheese/session",
            data={"grant": expired},
            headers={"Origin": settings.frontend_url},
        ).status_code
        == 401
    )
    assert (
        client.post(
            origin + "/_cheese/session",
            data={"grant": grant},
            headers={"Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.get("http://invalid.sites.localhost/health").status_code == 404
    assert client.get("http://sites.localhost/health").status_code == 404
    assert (
        client.post(
            f"/projects/{project}/site-session",
            headers=session_auth_headers("outsider"),
        ).status_code
        == 404
    )


def test_revoked_membership_blocks_existing_site_cookie(client, published):
    project, auth = published
    member = client.post(
        f"/projects/{project}/members",
        json={"user_handle": "bob", "role": "member"},
        headers=auth,
    )
    assert member.status_code == 200, member.text
    _open(client, project, session_auth_headers("bob"))
    origin = content_origin(project)
    assert client.get(origin + "/app.js").status_code == 200
    removed = client.delete(f"/projects/{project}/members/bob", headers=auth)
    assert removed.status_code == 200, removed.text
    assert client.get(origin + "/app.js").status_code == 404


def test_https_grant_sets_secure_host_cookie(client, published, monkeypatch):
    project, auth = published
    monkeypatch.setattr(settings, "sites_scheme", "https")
    _, response = _open(client, project, auth)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-cheese-site=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/" in cookie


def test_directory_links_and_login_keep_the_requested_site_path(client, published):
    project, auth = published
    origin = content_origin(project)
    response = client.get(
        origin + "/guide/?page=2",
        headers={"Sec-Fetch-Mode": "navigate"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "path=%2Fguide%2F%3Fpage%3D2" in response.headers["location"]
    grant = client.post(f"/projects/{project}/site-session", headers=auth).json()[
        "data"
    ]
    response = client.post(
        grant["url"],
        data={"grant": grant["grant"], "path": "/guide/?page=2"},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/guide/?page=2"
    response = client.get(origin + "/guide?page=2", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/guide/?page=2"
    assert client.get(origin + "/guide/").status_code == 200
    assert client.get(origin + "/guide/style.css").status_code == 200
    assert (
        client.post(
            grant["url"],
            data={"grant": grant["grant"], "path": "//attacker.example"},
            headers={"Origin": settings.frontend_url},
        ).status_code
        == 400
    )
