"""技能市场：两家的清单各自从官方 manifest 读，一个源倒掉不吃掉别的。

真网络不碰：`api.github.com` 和 MCP 注册表都用 MockTransport 替掉，只钉住市场读
了什么（manifest，而不是目录列举）、卡片长什么形状、钉住的 sha 有没有带出去，
以及某一源失败时 `notes` 有没有如实记下。
"""

import base64
import json

import httpx
import pytest

from app.core.config import settings
from app.domain import skill_market

SKILLS_MANIFEST = {
    "name": "anthropic-agent-skills",
    "plugins": [
        {
            "name": "document-skills",
            "description": "文档处理合集",
            "source": "./",
            "skills": ["./skills/pdf", "./skills/docx"],
        }
    ],
}

ANTHROPIC_PLUGINS = {
    "name": "claude-plugins-official",
    "plugins": [
        {
            "name": "agent-sdk-dev",
            "description": "开发 Agent SDK 用",
            "source": "./plugins/agent-sdk-dev",
        },
        {
            "name": "42crunch",
            "description": "API 安全",
            "source": {
                "source": "git-subdir",
                "url": "https://github.com/acme/plug.git",
                "path": "plug",
                "sha": "abc1234",
            },
        },
        {
            "name": "npm-thing",
            "description": "npm 包，没有能读的目录",
            "source": {"source": "npm", "package": "pkg"},
        },
    ],
}

OPENAI_PLUGINS = {
    "name": "openai-plugins",
    "plugins": [
        {
            "name": "linear",
            "source": {"source": "local", "path": "./plugins/linear"},
            "policy": {"installation": "AVAILABLE"},
            "category": "Productivity",
        },
        {
            "name": "gmail",
            "source": {"source": "local", "path": "./plugins/gmail"},
            "category": "Communication",
        },
    ],
}

MANIFESTS = {
    "/repos/anthropics/skills"
    "/contents/.claude-plugin/marketplace.json": SKILLS_MANIFEST,
    "/repos/anthropics/claude-plugins-official"
    "/contents/.claude-plugin/marketplace.json": ANTHROPIC_PLUGINS,
    "/repos/openai/plugins/contents/.agents/plugins/marketplace.json": OPENAI_PLUGINS,
}
FILES = {
    "/repos/anthropics/skills/contents/skills/pdf/SKILL.md": (
        "---\nname: pdf\ndescription: 读 PDF\n---\n\n正文。\n"
    ),
    "/repos/anthropics/skills/contents/skills/docx/SKILL.md": (
        "---\nname: docx\ndescription: 写 Word\n---\n\n正文。\n"
    ),
    "/repos/openai/plugins/contents/plugins/linear"
    "/.codex-plugin/plugin.json": json.dumps({"description": "Linear 集成"}),
    "/repos/openai/plugins/contents/plugins/gmail"
    "/.codex-plugin/plugin.json": json.dumps({"description": "Gmail 集成"}),
}


def _file(text: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _client(
    seen: list[str],
    *,
    broken: str = "",
    denied: str = "",
    dropped: str = "",
    drops: int = 1,
    auth: list | None = None,
    registry: list[dict] | None = None,
) -> httpx.AsyncClient:
    """A market backed by a fake network.

    ``dropped`` is a substring of the URL whose first ``drops`` requests end the
    connection instead of answering — the way this machine loses one now and
    then — and every later request is served as if nothing had happened.
    """
    dropped_times = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        host, path = request.url.host, request.url.path
        if dropped and dropped in str(request.url) and dropped_times["n"] < drops:
            dropped_times["n"] += 1
            raise httpx.ConnectError("connection reset")
        if host == "registry.modelcontextprotocol.io":
            if broken == "mcp":
                return httpx.Response(500)
            if registry is None:
                return httpx.Response(
                    200,
                    json={
                        "servers": [{"server": {"title": "Acme", "description": "d"}}]
                    },
                )
            # 照注册表的真样子作答：它是逐版本列出的，`version=latest` 才一台一条。
            rows = registry
            if dict(request.url.params).get("version") != "latest":
                rows = [row for row in registry for _ in row["versions"]]
            return httpx.Response(200, json={"servers": rows})
        if host != "api.github.com":
            return httpx.Response(404)
        if auth is not None:
            auth.append(request.headers.get("authorization"))
        if broken and broken in path:
            return httpx.Response(500)
        if denied and denied in path:
            return httpx.Response(403, json={"message": "API rate limit exceeded"})
        if path in MANIFESTS:
            return httpx.Response(200, json=_file(json.dumps(MANIFESTS[path])))
        if path in FILES:
            return httpx.Response(200, json=_file(FILES[path]))
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "")
    # 重试之间不真等：等待的时长本身不是这套测试要问的事。
    monkeypatch.setattr(skill_market, "READ_BACKOFF_S", 0)
    skill_market._CACHE.clear()
    # 每一条自带 client 的用例都绕开进程级连接池；跑完清掉，免得下一条借到
    # 一条已经关掉的连接。
    monkeypatch.setattr(skill_market, "_CLIENT", None)
    yield
    skill_market._CACHE.clear()
    skill_market._CLIENT = None


@pytest.mark.anyio
async def test_the_lists_come_from_each_vendors_own_manifest():
    seen: list[str] = []
    got = await skill_market.catalogue(_client(seen))

    # 技能：Anthropic 的 manifest 自己列出目录，说明读各自的 SKILL.md。
    assert [card["name"] for card in got["skills"]] == ["pdf", "docx"]
    assert [card["description"] for card in got["skills"]] == ["读 PDF", "写 Word"]
    assert got["skills"][0]["source"] == "anthropic"
    assert got["skills"][0]["kind"] == "skill"
    assert got["skills"][0]["url"].endswith("/anthropics/skills/tree/main/skills/pdf")

    # 插件：两家各一本目录。
    assert [card["name"] for card in got["plugins"]] == [
        "agent-sdk-dev",
        "42crunch",
        "npm-thing",
        "linear",
        "gmail",
    ]
    assert got["plugin_total"] == 5
    # Anthropic 的条目自带描述；OpenAI 的只有名字，读它自己的 plugin.json。
    assert got["plugins"][0]["description"] == "开发 Agent SDK 用"
    assert got["plugins"][3]["source"] == "openai"
    assert got["plugins"][3]["description"] == "Linear 集成"

    assert got["mcp"][0]["name"] == "Acme"
    assert got["mcp"][0]["kind"] == "mcp"
    assert got["notes"] == []
    # 没有目录列举这种请求：每份清单一个文件。
    assert not any("/contents/skills?" in url for url in seen)


@pytest.mark.anyio
async def test_the_pinned_sha_is_the_commit_a_card_installs():
    got = await skill_market.catalogue(_client([]))
    pinned = next(card for card in got["plugins"] if card["name"] == "42crunch")
    assert pinned["url"] == "https://github.com/acme/plug/tree/abc1234/plug"


def test_plugin_folders_follow_the_shapes_the_manifests_use():
    assert skill_market._plugin_folder(
        "./plugins/x", "anthropics", "claude-plugins-official"
    ) == ("https://github.com/anthropics/claude-plugins-official/tree/main/plugins/x")
    # Codex 的写法：一个对象指着同一仓库里的路径。
    assert (
        skill_market._plugin_folder(
            {"source": "local", "path": "./plugins/linear"}, "openai", "plugins"
        )
        == "https://github.com/openai/plugins/tree/main/plugins/linear"
    )
    # sha 比 ref 优先：那是审过的那个 commit。
    assert (
        skill_market._plugin_folder(
            {
                "source": "git-subdir",
                "url": "https://github.com/acme/plug.git",
                "path": "plug",
                "ref": "v1",
                "sha": "abc",
            },
            "o",
            "r",
        )
        == "https://github.com/acme/plug/tree/abc/plug"
    )
    # 装不来的：npm 包、以及指向仓库根的那种空路径。
    assert (
        skill_market._plugin_folder({"source": "npm", "package": "pkg"}, "o", "r") == ""
    )
    assert skill_market._plugin_folder("./", "o", "r") == ""


@pytest.mark.anyio
async def test_one_source_down_costs_only_its_own_list():
    got = await skill_market.catalogue(_client([], broken="claude-plugins-official"))

    assert [card["name"] for card in got["skills"]] == ["pdf", "docx"]
    assert [card["name"] for card in got["plugins"]] == ["linear", "gmail"]
    assert got["mcp"]
    assert any(note.startswith("anthropic plugins:") for note in got["notes"])


@pytest.mark.anyio
async def test_the_market_is_read_once_and_held():
    seen: list[str] = []
    client = _client(seen)
    await skill_market.catalogue(client)
    after_first = len(seen)
    await skill_market.catalogue(client)

    assert len(seen) == after_first  # 第二次一个字都没再发出去


@pytest.mark.anyio
async def test_descriptions_stop_at_the_first_rate_limit():
    """没有 token 时额度用完是可预期的：卡片照列，只是没有描述，原因写进 notes。"""
    got = await skill_market.catalogue(_client([], denied="SKILL.md"))

    assert [card["name"] for card in got["skills"]] == ["pdf", "docx"]
    assert all(card["description"] == "" for card in got["skills"])
    assert any("rate limit" in note for note in got["notes"])


@pytest.mark.anyio
async def test_a_refused_manifest_says_refused_and_not_missing():
    """403 和「没这个文件」是两回事：说成后者会让人去找一个就在那儿的文件。"""
    got = await skill_market.catalogue(_client([], denied="anthropics/skills/contents"))

    assert got["skills"] == []
    assert any("rate limit" in note and "GITHUB_TOKEN" in note for note in got["notes"])
    assert not any("not there" in note for note in got["notes"])


@pytest.mark.anyio
async def test_without_a_token_the_reads_are_anonymous():
    auth: list = []
    await skill_market.catalogue(_client([], auth=auth))
    assert auth and set(auth) == {None}


@pytest.mark.anyio
async def test_a_token_rides_along_and_lifts_the_description_budget(monkeypatch):
    monkeypatch.setattr(settings, "github_token", " ghp_test ")
    monkeypatch.setattr(skill_market, "DESCRIBE_LIMIT", 1)
    auth: list = []

    got = await skill_market.catalogue(_client([], auth=auth))

    assert set(auth) == {"Bearer ghp_test"}
    # 1 是没 token 时的上限；有 token 时不再砍，两张卡都有描述。
    assert [card["description"] for card in got["skills"]] == ["读 PDF", "写 Word"]


@pytest.mark.anyio
async def test_the_registry_is_asked_for_one_row_per_server():
    """注册表逐版本列出：不问 ``version=latest``，同一台服务器会重复出现。

    实测过：``limit=24`` 只得到 15 台不同的，``inference.sh`` 一台占 4 张卡。
    """
    registry = [
        {
            "server": {"title": "inference.sh", "description": "d"},
            "versions": ["0.1.0", "0.2.0", "0.3.0", "0.4.0"],
        },
        {"server": {"title": "docs-mcp", "description": "d"}, "versions": ["1.0.0"]},
    ]
    seen: list[str] = []
    got = await skill_market.catalogue(_client(seen, registry=registry))

    assert [card["name"] for card in got["mcp"]] == ["inference.sh", "docs-mcp"]
    assert any("version=latest" in url for url in seen)


@pytest.mark.anyio
async def test_a_dropped_connection_is_asked_again():
    """这台机器会把连接掐掉再连上：同一份清单再问一次就好，不该丢掉整个货架。

    实测 2026-10-26：到 ``api.github.com`` 的新连接有两三成被重置，重试之后就稳了。
    """
    seen: list[str] = []
    got = await skill_market.catalogue(
        _client(seen, dropped="skills/contents/.claude-plugin", drops=2)
    )

    assert [card["name"] for card in got["skills"]] == ["pdf", "docx"]
    assert got["notes"] == []
    # 两次被掐断，第三次答了。
    assert sum("skills/contents/.claude-plugin" in url for url in seen) == 3


@pytest.mark.anyio
async def test_the_registry_is_asked_again_when_the_connection_drops():
    got = await skill_market.catalogue(
        _client([], dropped="registry.modelcontextprotocol.io", drops=1)
    )

    assert got["mcp"][0]["name"] == "Acme"
    assert got["notes"] == []


@pytest.mark.anyio
async def test_a_shelf_lost_to_the_network_is_held_briefly_and_asked_again():
    """一次抖动只该空一小会儿：网络回来了，下次打开就要自己好，不用等半小时。"""
    seen: list[str] = []
    first = await skill_market.catalogue(
        _client(seen, dropped="skills/contents/.claude-plugin", drops=99)
    )
    assert first["skills"] == []
    assert any("no answer" in note for note in first["notes"])

    at, value, ttl = skill_market._CACHE["catalogue"]
    assert ttl < skill_market.CACHE_SECONDS  # 丢过货架的这一次不该留半小时
    # 一分钟后再打开，网络已经回来了：只把这次结果按时长老一分钟。
    skill_market._CACHE["catalogue"] = (at - 61, value, ttl)

    seen.clear()
    second = await skill_market.catalogue(_client(seen))

    assert [card["name"] for card in second["skills"]] == ["pdf", "docx"]
    assert second["notes"] == []
    assert seen  # 确实又问了一遍，不是把失败那份再端上来


@pytest.mark.anyio
async def test_a_refusal_is_answered_at_once_and_kept():
    """403 是对方答了：不再问，也不为它把额度反复花掉，照常留半小时。"""
    seen: list[str] = []
    got = await skill_market.catalogue(
        _client(seen, denied="anthropics/skills/contents")
    )

    assert got["skills"] == []
    assert sum("skills/contents/.claude-plugin" in url for url in seen) == 1
    assert skill_market._CACHE["catalogue"][2] == skill_market.CACHE_SECONDS


@pytest.mark.anyio
async def test_the_default_read_opens_one_pool_and_reuses_it(monkeypatch):
    """整份市场只开一条连接：这一台机器上失败的正是新建 TLS，不是请求本身。

    实测 2026-10-26（会话工作机）：到 api.github.com 新建 TLS 四十次里约十次被
    重置，而一条长连接上的四十个请求全部答了。市场一份清单一个文件、描述又十几个，
    让它们共用一条连接，是把这个失败面收掉的那一步。

    这条钉的是「没有传 client 的默认路径」——原来那里每读一次就新建一个 client。
    """
    caller = _client([])  # 调用方自带的那一个，在打桩之前就建好
    real = httpx.AsyncClient
    opened: list[httpx.AsyncClient] = []

    def counting(*args, **kwargs):
        made = real(*args, **kwargs)
        opened.append(made)
        return made

    monkeypatch.setattr(skill_market.httpx, "AsyncClient", counting)

    await skill_market.catalogue(caller)
    assert not opened  # 传进来的 client 是调用方的，默认路径不该再开

    # 两次默认读（绕开缓存），只应开一条：第二条是借来的，不是新开的。
    for _ in range(2):
        skill_market._CACHE.clear()
        await skill_market.catalogue()
    assert len(opened) == 1

    await skill_market.close_market_connections()


@pytest.mark.anyio
async def test_a_read_does_not_close_the_pool_it_borrowed():
    """读完了不关池子：它归进程所有，关掉就等于下一个人又要新建 TLS。"""
    await skill_market.catalogue(_client([]))
    pool = skill_market.reuse_market_connections()
    assert not pool.is_closed
    await skill_market.close_market_connections()


@pytest.mark.anyio
async def test_closing_lets_the_next_read_open_a_fresh_pool():
    first = skill_market.reuse_market_connections()
    await skill_market.close_market_connections()

    assert first.is_closed
    assert skill_market.reuse_market_connections() is not first
    await skill_market.close_market_connections()
