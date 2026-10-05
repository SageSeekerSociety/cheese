"""AI teammates search and read the docs (cheese_docs_search / cheese_docs_read),
over the real HTTP stack and database; the frontend that serves the docs is a
MockTransport that, like nginx, refuses dev/ without a valid pass.

What is pinned: every project's agents find the public pages; only a project
working on the platform's own repository finds developer pages, and anyone
else asking for one by name is refused, not answered empty; the backend's own
pass is what opens the gate, and a person's cookie is not needed.
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.docs_site import access, library, retrieval, site
from app.domain.project.models import ProjectForge
from tests.conftest import seed_user
from tests.integration.conftest import post_project

PUBLIC = [
    {
        "title": "验收与采纳",
        "heading": "采纳交付",
        "url": "/accept#is-merge",
        "text": "确认改动符合要求后，在任务面板中点击「采纳」。"
        "采纳并合并成功后，改动进入项目主线。",
    },
]
DEV = [
    {
        "title": "一条消息怎么变成芝士的一轮",
        "heading": "排队还是插话",
        "url": "/dev/turn#serialize",
        "text": "merge_into_running_turn 把新消息送进正在运行的会话，"
        "采纳之前的一轮不会被打断。",
    },
]


@pytest.fixture
def frontend(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The docs as the docs host serves them; dev/ only to a valid internal pass."""
    seen: dict = {"dev_refused": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/dev/"):
            cookie = request.headers.get("cookie", "")
            token = cookie.partition(f"{access.cookie_name()}=")[2].split(";")[0]
            if not access.is_internal(token):
                seen["dev_refused"] += 1
                return httpx.Response(401, text="gate")
        if path == "/ask-index.json":
            return httpx.Response(200, json=PUBLIC)
        if path == "/dev/ask-index.json":
            return httpx.Response(200, json=DEV)
        md = {
            "/accept.md": "# 验收与采纳\n\n采纳就是合并。",
            "/dev/turn.md": "# 一轮\n\n串行锁。",
        }
        if path in md:
            return httpx.Response(
                200,
                text=md[path],
                headers={"content-type": "text/markdown; charset=utf-8"},
            )
        return httpx.Response(
            404, text="<html>404</html>", headers={"content-type": "text/html"}
        )

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    # The docs on their own host: the backend reads the frontend container at
    # the site's root, and hands out links on the docs origin.
    monkeypatch.setattr(settings, "docs_origin", "https://docs.example.test")
    monkeypatch.setattr(retrieval, "source", retrieval.IndexSource(site.index_url))
    monkeypatch.setattr(
        retrieval,
        "dev_source",
        retrieval.IndexSource(site.dev_index_url, cookies=retrieval._internal_pass),
    )
    return seen


def _room(client, *, platform_repo: bool) -> tuple[str, dict[str, str]]:
    handle = f"doc-reader-{uuid.uuid4().hex[:6]}"
    headers = {"Authorization": f"Bearer {seed_user(client, handle)}"}
    r = post_project(client, json={"name": "P"}, headers=headers, owner=handle)
    assert r.status_code == 200, r.text
    project_id = r.json()["data"]["id"]
    if platform_repo:

        async def bind():
            # Every project is born with its forge; point it at the platform's
            # own repository.
            async with client.test_factory() as s:
                forge = await s.scalar(
                    select(ProjectForge).where(
                        ProjectForge.project_id == uuid.UUID(project_id)
                    )
                )
                forge.repo = "SageSeekerSociety/cheese"
                await s.commit()

        asyncio.run(bind())
    r = client.post(
        "/topics", json={"project_id": project_id, "title": "问文档"}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"], headers


def test_any_project_finds_the_public_pages(client, frontend):
    topic, headers = _room(client, platform_repo=False)
    r = client.post(
        "/docs/agent/search",
        json={"topic": topic, "query": "怎么采纳"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["dev"] is False
    [hit] = data["hits"]
    # Absolute, on the docs' own host, so a person can open it as is.
    assert hit["url"] == "https://docs.example.test/accept#is-merge"
    # The developer index was never even asked for.
    assert frontend["dev_refused"] == 0

    page = client.post(
        "/docs/agent/read", json={"topic": topic, "page": hit["url"]}, headers=headers
    )
    assert page.status_code == 200 and "采纳就是合并" in page.json()["data"]["markdown"]


def test_developer_pages_are_refused_outside_the_platforms_own_project(
    client, frontend
):
    topic, headers = _room(client, platform_repo=False)
    r = client.post(
        "/docs/agent/search",
        json={"topic": topic, "query": "串行 会话 采纳"},
        headers=headers,
    )
    assert all("/dev/" not in h["url"] for h in r.json()["data"]["hits"])
    denied = client.post(
        "/docs/agent/read", json={"topic": topic, "page": "dev/turn"}, headers=headers
    )
    assert denied.status_code == 403
    assert "知是自己的项目" in denied.json()["message"]


def test_the_platforms_own_project_reads_developer_pages_through_the_gate(
    client, frontend
):
    topic, headers = _room(client, platform_repo=True)
    r = client.post(
        "/docs/agent/search",
        json={"topic": topic, "query": "串行 会话"},
        headers=headers,
    )
    data = r.json()["data"]
    assert data["dev"] is True
    assert any(
        h["dev"] and h["url"] == "https://docs.example.test/dev/turn#serialize"
        for h in data["hits"]
    )
    page = client.post(
        "/docs/agent/read",
        json={"topic": topic, "page": "/docs/dev/turn#serialize"},
        headers=headers,
    )
    assert page.status_code == 200 and "串行锁" in page.json()["data"]["markdown"]
    # Every developer read passed the gate with the backend's own pass.
    assert frontend["dev_refused"] == 0


def test_the_internal_pass_opens_the_gate_and_nothing_else_does(client):
    # The backend's own read arrives as the frontend container, not as the
    # docs host: the pass is what opens the gate, whatever the host.
    passed = {"cookie": f"{access.cookie_name()}={access.internal_pass()}"}
    assert client.get("/docs/dev-access/check", headers=passed).status_code == 204
    # A platform access token signed with the same secret is not the pass.
    forged = {"cookie": f"{access.cookie_name()}={seed_user(client, 'not-a-pass')}"}
    assert client.get("/docs/dev-access/check", headers=forged).status_code == 401


def test_unknown_pages_and_bad_names(client, frontend):
    topic, headers = _room(client, platform_repo=False)
    missing = client.post(
        "/docs/agent/read",
        json={"topic": topic, "page": "no-such-page"},
        headers=headers,
    )
    assert missing.status_code == 404
    bad = client.post(
        "/docs/agent/read",
        json={"topic": topic, "page": "../etc/passwd"},
        headers=headers,
    )
    assert bad.status_code in (400, 422)


def test_page_names_are_normalised():
    assert library.page_slug("accept") == "accept"
    assert library.page_slug("/docs/accept#is-merge") == "accept"
    assert library.page_slug("https://okcheese.com/docs/dev/turn.md") == "dev/turn"
    assert library.page_slug("dev/turn") == "dev/turn"
    # The links the docs host hands out, and the ones from before it.
    assert library.page_slug("/accept#is-merge") == "accept"
    assert library.page_slug("https://docs.okcheese.com/dev/turn#x") == "dev/turn"
    for bad in ("../x", "dev/../x", "a b", "", "/docs/dev/a/b"):
        assert library.page_slug(bad) is None
