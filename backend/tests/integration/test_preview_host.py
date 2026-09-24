"""Preview sessions isolate topic content from platform and published Site access."""

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.preview_host import cookie_name, mint_preview_token, preview_origin
from app.common.auth import verify_access_token
from app.core.config import settings
from app.domain.library import service as library
from app.domain.site.hosting import content_origin, mint_site_token
from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    session_auth_headers,
    session_token,
)
from tests.integration.test_app_preview_proxy import (
    _open_preview,
    _project_topic,
)
from tests.integration.test_app_preview_proxy import (
    preview_config as preview_config,
)


@pytest.fixture
def static_preview(client, preview_config):
    project, topic = _project_topic(client)
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    html = '<script type="module" src="/main.js"></script><img src="/image.svg">'
    response = client.post(
        f"/topics/{topic_id}/shown",
        json={"path": "web/report.html", "as": "html", "content": html},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    assets = {
        "main.js": 'import "/dependency.js";',
        "dependency.js": "export const value = 1;",
        "style.css": 'body { background: url("/image.svg") }',
        "image.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>',
        "nested/asset.txt": "nested asset",
    }
    for path, content in assets.items():
        library.write_room_file(project_id, topic_id, "web/" + path, content.encode())
    return project_id, topic_id, html, assets


def test_preview_session_opens_selected_artifact_and_assets(client, static_preview):
    _, topic_id, html, assets = static_preview
    grant, exchange = _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    assert grant["url"] == origin + "/_cheese/session"
    assert "grant=" not in grant["url"] and "token=" not in grant["url"]
    assert exchange.headers["location"] == "/"
    cookie = exchange.headers["set-cookie"]
    assert cookie.startswith("cheese-preview-local=")
    assert "HttpOnly" in cookie and "Path=/" in cookie and "Domain=" not in cookie
    response = client.get(origin + "/?theme=dark")
    assert response.status_code == 200 and response.text == html
    assert "allow-same-origin" in response.headers["content-security-policy"]
    assert "worker-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    for path, content in assets.items():
        response = client.get(origin + "/" + path + "?v=one&v=two")
        assert response.status_code == 200, (path, response.text)
        assert response.text == content
    head = client.head(origin + "/")
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(html.encode())
    assert client.post(origin + "/", content=b"overwrite").status_code == 405
    assert client.get(origin + "/api/projects").status_code == 404
    assert (
        client.get(
            origin + "/main.js", headers={"Service-Worker": "script"}
        ).status_code
        == 403
    )


def test_https_preview_cookie_is_host_only_secure_and_partitioned(
    client, static_preview, monkeypatch
):
    _, topic_id, _, _ = static_preview
    monkeypatch.setattr(settings, "sites_scheme", "https")
    _, response = _open_preview(client, topic_id)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-cheese-preview=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=none", "Partitioned"):
        assert attribute.lower() in cookie.lower(), cookie
    assert "Domain=" not in cookie
    assert client.get(preview_origin(topic_id) + "/").status_code == 200


def test_grant_is_topic_scoped_expiring_and_separate_from_platform_and_site_tokens(
    client, static_preview
):
    project_id, topic_id, _, _ = static_preview
    owner = session_auth_headers("alice")
    response = client.post(f"/topics/{topic_id}/preview-session", headers=owner)
    assert response.status_code == 200, response.text
    grant = response.json()["data"]
    assert response.headers["cache-control"] == "no-store"
    tokens = [
        session_token("alice"),
        mint_preview_token(topic_id, "alice", purpose="preview-grant", ttl=-1),
        mint_preview_token(topic_id, "alice", purpose="preview-session", ttl=60),
        mint_site_token(project_id, "alice", purpose="site-grant", ttl=60),
    ]
    for token in tokens:
        exchange = client.post(
            grant["url"],
            data={"grant": token},
            headers={"Origin": settings.frontend_url},
            follow_redirects=False,
        )
        assert exchange.status_code == 401, exchange.text
        assert "set-cookie" not in exchange.headers
    wrong_host = preview_origin(uuid.uuid4())
    assert (
        client.post(
            wrong_host + "/_cheese/session",
            data={"grant": grant["grant"]},
            headers={"Origin": settings.frontend_url},
            follow_redirects=False,
        ).status_code
        == 401
    )
    assert verify_access_token(grant["grant"]) is None
    _, exchange = _open_preview(client, topic_id)
    preview_session = exchange.cookies.get(cookie_name())
    assert verify_access_token(preview_session) is None
    client.headers.clear()
    for token in (grant["grant"], preview_session):
        assert (
            client.post(
                f"/topics/{topic_id}/preview-session",
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 401
        )
        assert (
            client.post(
                content_origin(project_id) + "/_cheese/session",
                data={"grant": token},
                headers={"Origin": settings.frontend_url},
                follow_redirects=False,
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    "credential", ["platform", "grant", "expired", "wrong-topic", "site"]
)
def test_only_matching_preview_session_cookie_reads_content(
    client, static_preview, credential
):
    project_id, topic_id, _, _ = static_preview
    tokens = {
        "platform": session_token("alice"),
        "grant": mint_preview_token(topic_id, "alice", purpose="preview-grant", ttl=60),
        "expired": mint_preview_token(
            topic_id, "alice", purpose="preview-session", ttl=-1
        ),
        "wrong-topic": mint_preview_token(
            uuid.uuid4(), "alice", purpose="preview-session", ttl=60
        ),
        "site": mint_site_token(project_id, "alice", purpose="site-session", ttl=60),
    }
    response = client.get(
        preview_origin(topic_id) + "/",
        headers={"Cookie": cookie_name() + "=" + tokens[credential]},
    )
    assert response.status_code == 401, response.text


def test_session_exchange_requires_post_and_platform_origin(client, static_preview):
    _, topic_id, _, _ = static_preview
    grant = client.post(
        f"/topics/{topic_id}/preview-session", headers=session_auth_headers("alice")
    ).json()["data"]
    assert client.get(grant["url"], params={"grant": grant["grant"]}).status_code == 405
    for origin in (None, "null", preview_origin(topic_id), "https://attacker.example"):
        response = client.post(
            grant["url"],
            data={"grant": grant["grant"]},
            headers={} if origin is None else {"Origin": origin},
            follow_redirects=False,
        )
        assert response.status_code == 403, response.text
        assert "set-cookie" not in response.headers


def test_navigation_preserves_path_without_putting_credentials_in_url(
    client, static_preview
):
    _, topic_id, _, _ = static_preview
    response = client.get(
        preview_origin(topic_id) + "/nested/asset.txt?x=one%2Ftwo&x=2",
        headers={"Sec-Fetch-Mode": "navigate"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.scheme + "://" + location.netloc == settings.frontend_url
    assert location.path == f"/previews/{topic_id}"
    path = parse_qs(location.query)["path"][0]
    assert path == "/nested/asset.txt?x=one%2Ftwo&x=2"
    grant, exchange = _open_preview(client, topic_id, path=path)
    assert exchange.headers["location"] == path
    assert grant["grant"] not in exchange.headers["location"]
    assert client.get(preview_origin(topic_id) + path).text == "nested asset"


@pytest.mark.parametrize(
    "path",
    [
        "//attacker.example",
        "https://attacker.example",
        "/\\attacker.example",
        "/\r\nlocation: evil",
    ],
)
def test_exchange_rejects_external_or_header_injecting_return_paths(
    client, static_preview, path
):
    _, topic_id, _, _ = static_preview
    grant = client.post(
        f"/topics/{topic_id}/preview-session", headers=session_auth_headers("alice")
    ).json()["data"]
    response = client.post(
        grant["url"],
        data={"grant": grant["grant"], "path": path},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert response.status_code == 400, response.text


def test_static_preview_tracks_selected_artifact_directory(client, static_preview):
    project_id, topic_id, _, _ = static_preview
    _open_preview(client, topic_id)
    response = client.post(
        f"/topics/{topic_id}/shown",
        json={
            "path": "other/page.html",
            "as": "html",
            "content": "selected second page",
        },
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    library.write_room_file(project_id, topic_id, "other/main.js", b"second module")
    origin = preview_origin(topic_id)
    assert client.get(origin + "/").text == "selected second page"
    assert client.get(origin + "/main.js").text == "second module"
    assert client.get(origin + "/image.svg").status_code == 404
    assert client.get(origin + "/web/report.html").status_code == 404


def test_static_preview_rejects_hidden_traversal_and_symlink_escape(
    client, static_preview
):
    project_id, topic_id, _, _ = static_preview
    _open_preview(client, topic_id)
    tree = library.room_files_root(project_id, topic_id)
    (tree / "secret.txt").write_text("outside selected directory")
    (tree / "web/.env").write_text("private value")
    (tree / "web/.hidden").mkdir()
    (tree / "web/.hidden/data.txt").write_text("hidden value")
    (tree / "web/escape.txt").symlink_to(tree / "secret.txt")
    (tree / "web/escape-dir").symlink_to(tree, target_is_directory=True)
    for path in (
        "/.env",
        "/.hidden/data.txt",
        "/.git/config",
        "/%2e%2e/secret.txt",
        "/%2e%2e%2fsecret.txt",
        "/..%5csecret.txt",
        "/escape.txt",
        "/escape-dir/secret.txt",
    ):
        response = client.get(preview_origin(topic_id) + path)
        assert response.status_code == 404, (path, response.status_code, response.text)
    selected = client.post(
        f"/topics/{topic_id}/shown",
        json={"path": "web/escape.txt", "as": "html"},
        headers=session_auth_headers("alice"),
    )
    assert selected.status_code == 200, selected.text
    assert client.get(preview_origin(topic_id) + "/").status_code == 404


@pytest.fixture
def room_file_preview(client, preview_config):
    """一个房间，有文件、但还没有 artifact。

    人传上来的页面和芝士写下还没摆出来的东西都是这个形状——预览面板要把它们画出来，
    不能先要求房间摆过东西。
    """
    project, topic = _project_topic(client)
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    library.write_room_file(
        project_id, topic_id, "pages/site.html", b"<h1>room page</h1>"
    )
    library.write_room_file(
        project_id, topic_id, "pages/style.css", b"h1 { color: red }"
    )
    library.write_room_file(
        project_id,
        topic_id,
        "pages/logo.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    )
    return project_id, topic_id


def test_a_room_file_renders_without_any_artifact(client, room_file_preview):
    _, topic_id = room_file_preview
    origin = preview_origin(topic_id)
    # 带 cookie 之前：一次裸访问回到平台换授权，地址原样带过去——预览域的地址里
    # 从来不放凭据，这一步是它成立的原因。
    bare = client.get(
        origin + "/_cheese/room/pages/site.html",
        headers={"Sec-Fetch-Mode": "navigate"},
        follow_redirects=False,
    )
    assert bare.status_code == 303
    location = urlsplit(bare.headers["location"])
    assert location.scheme + "://" + location.netloc == settings.frontend_url
    assert location.path == f"/previews/{topic_id}"
    assert parse_qs(location.query)["path"][0] == "/_cheese/room/pages/site.html"

    _, exchange = _open_preview(client, topic_id, path="/_cheese/room/pages/site.html")
    assert exchange.headers["location"] == "/_cheese/room/pages/site.html"
    page = client.get(origin + "/_cheese/room/pages/site.html")
    assert page.status_code == 200, page.text
    assert page.text == "<h1>room page</h1>"
    assert page.headers["content-type"].startswith("text/html")
    # 页面里的相对资源落在同一份文件旁边，而且和页面同源——iframe 里的相对引用
    # 才不用改写。
    style = client.get(origin + "/_cheese/room/pages/style.css")
    assert style.status_code == 200 and style.text == "h1 { color: red }"
    assert style.headers["content-type"].startswith("text/css")
    logo = client.get(origin + "/_cheese/room/pages/logo.svg")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/svg+xml")
    head = client.head(origin + "/_cheese/room/pages/site.html")
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(b"<h1>room page</h1>")
    assert client.post(origin + "/_cheese/room/pages/site.html").status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "/_cheese/room/",
        "/_cheese/room/pages/missing.html",
        "/_cheese/room/pages",
        "/_cheese/room/.env",
        "/_cheese/room/pages/.hidden/secret.txt",
        "/_cheese/room/%2e%2e/secret.txt",
        "/_cheese/room/pages/escape.txt",
        "/_cheese/room/pages/escape-dir/other-room.txt",
    ],
)
def test_the_room_file_address_stays_inside_the_room(client, room_file_preview, path):
    project_id, topic_id = room_file_preview
    tree = library.room_files_root(project_id, topic_id)
    (tree / ".env").write_text("private value")
    (tree / "pages/.hidden").mkdir()
    (tree / "pages/.hidden/secret.txt").write_text("hidden value")
    # 隔壁房间那一份：能指到它才算真逃出来了。同一个房间树里面的符号链接不算
    # 逃逸——读者本来就能按名字读到那份文件。
    (tree.parent / "other-room.txt").write_text("another room")
    (tree / "pages/escape.txt").symlink_to(tree.parent / "other-room.txt")
    (tree / "pages/escape-dir").symlink_to(tree.parent, target_is_directory=True)
    # 这个房间没有 artifact，所以换授权也只能冲着这份房间文件去。
    _open_preview(client, topic_id, path="/_cheese/room/pages/site.html")
    response = client.get(preview_origin(topic_id) + path)
    assert response.status_code == 404, (path, response.status_code, response.text)


def test_a_room_file_is_addressed_by_its_own_path_not_the_artifacts(
    client, static_preview
):
    project_id, topic_id, _, _ = static_preview
    library.write_room_file(
        project_id, topic_id, "other/page.html", b"<p>elsewhere</p>"
    )
    _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    # artifact 所在目录之外：老地址取不到（这是它一贯的边界）。
    assert client.get(origin + "/other/page.html").status_code == 404
    # 房间文件按房间相对路径寻址，和 artifact 是谁无关。
    assert (
        client.get(origin + "/_cheese/room/other/page.html").text == "<p>elsewhere</p>"
    )


def test_revocation_blocks_an_already_issued_grant_and_cookie(client, static_preview):
    project_id, topic_id, _, _ = static_preview
    owner = session_auth_headers("alice")
    add_external_member(client, project_id, "bob", by="alice")
    grant, _ = _open_preview(client, topic_id, "bob")
    assert client.get(preview_origin(topic_id) + "/").status_code == 200
    assert (
        client.delete(f"/projects/{project_id}/members/bob", headers=owner).status_code
        == 200
    )
    assert client.get(preview_origin(topic_id) + "/").status_code == 404
    assert (
        client.post(
            grant["url"],
            data={"grant": grant["grant"]},
            headers={"Origin": settings.frontend_url},
            follow_redirects=False,
        ).status_code
        == 404
    )


@pytest.mark.parametrize("authz_enabled", [True, False])
@pytest.mark.parametrize("dispatch_task", [False, True])
def test_private_room_roster_is_required_even_for_project_members(
    client, preview_config, monkeypatch, authz_enabled, dispatch_task
):
    monkeypatch.setattr(settings, "authz_enforce_topic_access", authz_enabled)
    project, _ = _project_topic(client)
    owner = session_auth_headers("alice")
    for handle in ("bob", "outsider"):
        join_project_team(client, project["id"], handle)
    response = client.get(
        f"/projects/{project['id']}/private-chat",
        params={"user_handle": "alice", "peer_handle": "bob"},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    room_id = response.json()["data"]["id"]
    artifact = client.post(
        f"/topics/{room_id}/shown",
        json={"path": "web/private.html", "as": "html", "content": "private preview"},
        headers=owner,
    )
    assert artifact.status_code == 200, artifact.text
    preview_id = room_id
    if dispatch_task:
        task = client.post(
            f"/topics/{room_id}/split",
            json=dict(reviewer_handle="alice", **{"title": "Private work"}),
            headers=owner,
        )
        assert task.status_code == 200, task.text
        preview_id = task.json()["data"]["id"]
    assert (
        client.post(
            f"/topics/{preview_id}/preview-session",
            headers=session_auth_headers("outsider"),
        ).status_code
        == 404
    )
    if dispatch_task:
        # A task card does not own a room or a separate preview origin.
        assert (
            client.post(
                f"/topics/{preview_id}/preview-session",
                headers=session_auth_headers("bob"),
            ).status_code
            == 404
        )
        return
    _open_preview(client, preview_id, "bob")
    origin = preview_origin(uuid.UUID(preview_id))
    assert client.get(origin + "/").text == "private preview"
    removed = client.delete(f"/topics/{room_id}/members/bob", headers=owner)
    assert removed.status_code == 200, removed.text
    assert client.get(origin + "/").status_code == 404


@pytest.mark.parametrize(
    "route",
    ["app/", "app/main.js", "app-session", "preview/raw", "preview/raw/main.js"],
)
def test_platform_no_longer_serves_preview_content(client, static_preview, route):
    _, topic_id, _, _ = static_preview
    response = client.get(
        f"/topics/{topic_id}/{route}", headers=session_auth_headers("alice")
    )
    assert response.status_code == 404, response.text
