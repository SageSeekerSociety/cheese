"""芝士 CLI 在一条支线里必须整套都能用。

一件活现在是房间里的一条支线，而干活的分身拿到的 `CHEESE_TOPIC` 就是那条支线的
id（`tmux_provider` 就是这么设的）。所以 CLI 打出去的每一个 `/topics/{id}/…` 都
带着支线的 id，只认房间的那些会 404——分身直接失去这条命令。

`title` 比 404 更糟：它不报错。一条支线给自己起名字，改的是整个房间的名字，而
两边都不会有任何提示。

所以这个文件按 `backend/sandbox/cheese` 里**实际写着的路径**一条条对，而不是按
路由表——CLI 打不通的路由才是分身真正失去的能力。
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers


def _room(client) -> tuple[str, str]:
    """一个房间，外加它所属的项目。"""
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间原名", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _as_agent(project_id: str, place_id: str) -> dict:
    """分身手里那张 per-turn token —— 按它所在的**地方**签的，支线就是支线的 id。"""
    return {
        "X-Cheese-Token": mint_scoped_token(project_id=project_id, topic_id=place_id)
    }


def _blocks(client, place_id: str) -> list[dict]:
    return client.get(f"/topics/{place_id}/blocks").json()["data"]["data"]


def _contents(client, place_id: str) -> list[str]:
    return [b.get("content") or "" for b in _blocks(client, place_id)]


# --- cheese title ----------------------------------------------------------


def test_naming_the_work_does_not_rename_the_room(client):
    """`cheese title` 是分身开工第一件事，所以这条错了影响面最大。

    支线给自己起名字，房间的名字必须一个字都不动。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/title",
        json={"title": "查一下分页接口"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title"] == "查一下分页接口"

    assert client.get(f"/topics/{room}").json()["data"]["title"] == "房间原名"


def test_naming_a_room_still_names_the_room(client):
    """另一半：房间自己改名还得照常работать——上面那条不能是把整条路关掉换来的。"""
    pid, room = _room(client)

    r = client.post(
        f"/topics/{room}/title",
        json={"title": "房间新名"},
        headers=_as_agent(pid, room),
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/topics/{room}").json()["data"]["title"] == "房间新名"


# --- cheese doc set / doc get ----------------------------------------------


def test_a_thread_writes_its_own_doc(client):
    """`cheese doc set` 写的是这条支线的实况文档，房间那份不受影响。

    读那一半（`GET /doc`）本来就认支线，写这一半不认——一个分身能读到自己的简报、
    却写不回去。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    before = client.get(f"/topics/{room}/doc").json()["data"]
    room_doc_before = (before or {}).get("content")

    current = client.get(f"/topics/{thread}/doc").json()["data"]
    r = client.put(
        f"/topics/{thread}/doc",
        json={
            "content": "# 进展\n\n查完了，结论写在这。",
            "expected_version": (current or {}).get("doc_version", 0),
        },
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text

    assert "结论写在这" in client.get(f"/topics/{thread}/doc").json()["data"]["content"]
    after = client.get(f"/topics/{room}/doc").json()["data"]
    assert (after or {}).get("content") == room_doc_before


# --- cheese ask / decision / artifact --------------------------------------


def test_ask_lands_in_the_thread_not_the_room(client):
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/ask",
        json={"question": "用 A 还是 B？", "options": ["A", "B"]},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["meta"]["options"] == ["A", "B"]

    assert any("用 A 还是 B？" in c for c in _contents(client, thread))
    assert not any("用 A 还是 B？" in c for c in _contents(client, room))


def test_decision_lands_in_the_thread_not_the_room(client):
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/decision",
        json={"decision": "改走游标分页"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text

    assert any("改走游标分页" in c for c in _contents(client, thread))
    assert not any("改走游标分页" in c for c in _contents(client, room))


def test_artifact_becomes_the_threads_preview_not_the_rooms(client):
    """An artifact is not a timeline block — it is read back as this place's
    preview, so that is where it has to show up."""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/artifact",
        json={"path": "report.html", "as": "html"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text

    shown = client.get(
        f"/topics/{thread}/preview", headers=_as_agent(pid, thread)
    ).json()["data"]
    assert shown is not None and shown["path"] == "report.html"
    # 每条支线预览自己的结果：房间的预览不会被随便哪条支线覆盖掉。
    assert (
        client.get(f"/topics/{room}/preview", headers=_as_agent(pid, room)).json()[
            "data"
        ]
        is None
    )


# --- cheese status ---------------------------------------------------------


def test_status_describes_the_thread_it_was_asked_about(client):
    """盲飞防护是干活的人在问，所以答的必须是它自己那条线。

    答成房间的话，分身会拿着房间的标题和分支去判断自己在干什么——比 404 难发现。
    """
    pid, room = _room(client)
    thread = _thread(client, room, title="写迁移脚本")

    r = client.get(f"/topics/{thread}/status", headers=_as_agent(pid, thread))
    assert r.status_code == 200, r.text
    reported = r.json()["data"]["topic"]

    assert reported["id"] == thread
    assert reported["title"] == "写迁移脚本"


# --- cheese await ----------------------------------------------------------


def test_a_long_command_can_be_registered_from_a_thread(client):
    """`cheese await` 是跑全量测试那种长命令的唯一正路；支线用不了它，就只能
    傻等到轮次被掐。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/background-task",
        json={"command": "pytest -q", "label": "全量", "timeout_s": 60},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["task_id"]


# --- cheese accept-request -------------------------------------------------


def test_work_done_in_a_thread_can_be_filed_for_acceptance(client):
    """`cheese accept-request` 是一条支线交活的唯一出口——递不出卡，这条线做完的
    东西就没有任何路径能进主干。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/accept-card",
        json={
            "change_subject": "fix(api): return the last row of a page",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text

    filed = client.get(f"/topics/{thread}/accept-card").json()["data"]["data"]
    assert [c["change_subject"] for c in filed] == [
        "fix(api): return the last row of a page"
    ]


# --- cheese remember / recall ----------------------------------------------


def test_what_a_thread_learns_is_readable_from_its_room(client):
    """记忆按 agent 存，而支线开工时继承了房间的 agent —— 房间派活出去，图的就是
    这个：支线学到的东西，房间回头读得到。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/projects/{pid}/memory",
        json={"content": "分页接口有个 N+1", "topic": thread},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text

    hits = client.post(
        f"/projects/{pid}/memory/search",
        json={"query": "N+1", "topic": room},
        headers=_as_agent(pid, room),
    ).json()["data"]["hits"]
    assert [h["abstract"] for h in hits] == ["分页接口有个 N+1"]


# --- 人也要能照常用 --------------------------------------------------------


def test_a_person_can_still_reach_a_thread(client):
    """这些路由是双用的。上面全是分身在调，这条确认人拿自己的登录态也能读到
    一条支线——否则等于把支线做成了只有 AI 能进的地方。"""
    _pid, room = _room(client)
    thread = _thread(client, room, title="给人看的活")

    r = client.get(f"/topics/{thread}/status", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["topic"]["title"] == "给人看的活"
