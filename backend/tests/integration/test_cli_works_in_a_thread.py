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
from tests.conftest import wait_work_idle
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


def test_a_thread_reads_its_own_brief_holding_its_own_token(client):
    """`cheese doc get` —— 分身开工要读的第一样东西，也是 `doc set` 的前置。

    这条路由的失败方式和别处不同，所以单列：不是 404，是 **403「这个 token 属于
    别的话题」**。分身手里那张 per-turn token 是按它所在的**地方**签的（支线），
    而这条路拿**房间**的 id 去校验它，两个 id 都是合法 uuid，谁都不会报错。

    上面那条测试读文档时不带 token，正好绕开了这一步——所以它一直是绿的，而真实
    的分身第一条命令就打不通。
    """
    pid, room = _room(client)
    thread = _thread(client, room, title="查一下分页接口")

    r = client.get(f"/topics/{thread}/doc", headers=_as_agent(pid, thread))
    assert r.status_code == 200, r.text
    assert "查一下分页接口" in (r.json()["data"] or {}).get("content", "")


def test_a_thread_reads_its_own_timeline_holding_its_own_token(client):
    """`cheese api GET /topics/{id}/blocks` —— 分身回看自己说过什么的唯一路子。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    client.post(
        f"/topics/{thread}/decision",
        json={"decision": "改走游标分页"},
        headers=_as_agent(pid, thread),
    )

    r = client.get(f"/topics/{thread}/blocks", headers=_as_agent(pid, thread))
    assert r.status_code == 200, r.text
    assert any(
        "改走游标分页" in (b.get("content") or "") for b in r.json()["data"]["data"]
    )


# --- cheese conclude -------------------------------------------------------


def test_a_thread_returns_its_conclusion_holding_its_own_token(client):
    """`cheese conclude` 是一条支线**唯一**的回话出口——房间读不到它的对话，
    结论不回流，这条线做的一切在房间里就是没发生过。

    和上面同一个病：token 按支线签，校验按房间做。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/return-conclusion",
        json={"conclusion": "分页接口有个 N+1，改法写在文档里"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert any("N+1" in c for c in _contents(client, room))


# --- cheese tell / cheese split --------------------------------------------


def test_a_thread_can_speak_to_its_room(client):
    """`cheese tell` —— 支线给房间捎一句话。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/tell",
        json={"target": "parent", "content": "这块我接手了"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert any("这块我接手了" in c for c in _contents(client, room))


def test_a_thread_can_dispatch_work_of_its_own(client):
    """`cheese split` —— 干一件活时发现第二件，是常态。活不嵌套，所以新的那条
    挂在**同一个房间**下。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/split",
        json={"title": "顺带把索引补上"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["room_id"] == room


# --- cheese notify ---------------------------------------------------------


def test_a_thread_can_send_a_notification(client):
    """`cheese notify` —— 「人不在这个话题里也得知道」的那条路。

    它坏得和上面几条不一样：不是 403 而是**裸 500**。通知的 `topic_id` 是指向
    `topics` 的外键，而 CLI 送来的是分身所在的**地点** id；支线不是 `topics` 的
    一行（把活从话题里拆出来的那次迁移把它删了），于是外键违例冒成服务器错误，
    屏幕上没有任何东西说明为什么。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "title": "该看一眼了",
            "body": "",
            "level": "light",
            "kind": "change_alert",
            "topic_id": thread,
        },
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    # 指针指向房间：通知说的是「去哪儿看」，而一件活就住在一个房间里。
    assert r.json()["data"]["topic_id"] == room


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


# --- 分身带着自己那张 token 调 -----------------------------------------------
#
# 上面每一条都验了「路由认不认支线的 id」，但大多没带 header —— 匿名请求根本不
# 触发作用域检查。分身不是匿名的：它手里那张 per-turn token 是按**支线**签的，
# 而一条路由若拿房间的 id 去认这张 token，得到的是 403「这个 token 属于别的话题」。
# 症状和 404 完全不同（路由是通的，是它不认这个人），所以要单独钉。


def _cli_read_paths(place_id: str) -> list[str]:
    """CLI 和界面读一个地方时会打的每一条 GET。"""
    return [
        f"/topics/{place_id}",
        f"/topics/{place_id}/blocks",
        f"/topics/{place_id}/doc",
        f"/topics/{place_id}/docs",
        f"/topics/{place_id}/progress",
    ]


def test_a_thread_reads_itself_with_its_own_token(client):
    """`cheese doc get` 是这里面最要命的一条。

    它 403 之后 `cheese doc set` 也跟着废——写要求版本号必须来自一次真实的读，
    唯一的读路径被拒，于是这条支线永远改不了自己的实况文档。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    for path in _cli_read_paths(thread):
        r = client.get(path, headers=_as_agent(pid, thread))
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"


def test_a_room_still_reads_itself_with_its_own_token(client):
    """另一半：房间自己那张 token 照常好使——上面那条不能是把检查关掉换来的。"""
    pid, room = _room(client)

    for path in _cli_read_paths(room):
        r = client.get(path, headers=_as_agent(pid, room))
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"


def test_a_token_from_another_place_is_still_refused(client):
    """作用域检查本身要留着：拿甲支线的 token 去读乙支线，必须还是 403。"""
    pid, room = _room(client)
    mine = _thread(client, room, title="我的活")
    yours = _thread(client, room, title="别人的活")

    r = client.get(f"/topics/{yours}/doc", headers=_as_agent(pid, mine))
    assert r.status_code == 403, r.text


def test_a_thread_returns_its_conclusion_with_its_own_token(client):
    """`cheese conclude` 是一条支线把结论送回房间的唯一通道。

    它 403 的时候，这条支线做完的活一个字也回不去——而它自己是不会知道的，
    因为报错只落在那一轮的终端里。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/return-conclusion",
        json={"conclusion": "查完了：分页少返一行，改游标就好"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    assert any("分页少返一行" in c for c in _contents(client, room))


def test_a_thread_tells_its_room_with_its_own_token(client):
    """`cheese tell` 是 conclude 之外那条「说句话就走」的路。

    两条一起断的时候，房间还会建议分身「回话用 cheese tell」——而那正是坏的
    那条，于是建议本身把人引进死胡同。
    """
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/tell",
        json={"target": room, "content": "先说一声：这条路能通。"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    assert any("这条路能通" in c for c in _contents(client, room))


def test_a_thread_can_dispatch_more_work(client):
    """`cheese split` 从支线里发得出去——这条路由的说明本来就写着 topic_id 可以
    是支线的（干一件活时常常发现第二件）。活不嵌套，新的那条挂在同一个房间下。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/split",
        json={"title": "顺手发现的第二件活"},
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()
    assert r.json()["data"]["room_id"] == room


# --- cheese notify ---------------------------------------------------------


def test_a_thread_can_send_a_notification(client):
    """`cheese notify` 无条件把 `$CHEESE_TOPIC` 当 `topic_id` 发出去，而那是支线
    的 id。`alerts.topic_id` 是指向 `topics` 的外键，支线不在那张表里——于是整条
    命令 500，连最小的一条通知都发不出去。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "light",
            "kind": "change_alert",
            "title": "改完了",
            "topic_id": thread,
        },
        headers=_as_agent(pid, thread),
    )
    assert r.status_code == 200, r.text
    # 通知指向的是这条支线所在的房间——点开它，人得落在一个打得开的地方。
    assert r.json()["data"]["topic_id"] == room


# --- 人也要能照常用 --------------------------------------------------------


def test_a_person_can_still_reach_a_thread(client):
    """这些路由是双用的。上面全是分身在调，这条确认人拿自己的登录态也能读到
    一条支线——否则等于把支线做成了只有 AI 能进的地方。"""
    _pid, room = _room(client)
    thread = _thread(client, room, title="给人看的活")

    r = client.get(f"/topics/{thread}/status", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["topic"]["title"] == "给人看的活"
