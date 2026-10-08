"""没有项目记忆池：所有人都该看见的东西是一份文档（结论 7）。

一个共享池和一份文档差的不是存在哪儿。池子没有主、不留痕、人翻不到也改不了，
而它偏偏又是「大家都该知道的事」唯一的落点——于是项目的共识住在一个只有模型读
得到的地方。文档三样都有，所以那一路的去向是项目总览。

写记忆的那个旧入口已经整个停用（话题「记忆机制照搬CC」）：先是 `cheese_remember
everyone` 往这份文档里追加的那一路——它把总览写成了只增不减的观察清单——接着是
`cheese_remember` 本身，它写的条目池已经不再注入任何地方。这一组守的是还成立的那
几头：停用要明说、并且说清该去哪写；每段对话跑一轮时，总览在它的提示词里。

总览只有「项目是什么」那一块进提示词：手抄进别的小节的字谁都读不到。
"""

from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)
from tests.support.living_doc import overview_of

FACT = "中期答辩定在 11 月 15 日，要现场演示一个能跑的 demo"


def _overview(client, project_id: str) -> str:
    """The project's overview, as the document routes address it."""
    return f"/documents/{overview_of(client, project_id)}"


def _project_and_room(client) -> tuple[str, str]:
    project_id = post_project(client, json={"name": "P"}, owner="user-1").json()[
        "data"
    ]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "干活的房间"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]["id"]
    return project_id, topic_id


def _say(client, topic_id: str, text: str = "@芝士 现在什么状态") -> None:
    """在这个房间的一条支线里叫芝士说一句，等它跑完——人叫芝士，芝士在支线里
    答。提示词落在 `stub_hooks` 上。"""
    thread = in_thread(client, topic_id, "user-1")
    with room_socket(client, thread, "user-1") as ws:
        post_message(client, thread, "user-1", {"content": text})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def _overview_text(client, project_id: str) -> str:
    doc = client.get(_overview(client, project_id)).json()["data"]
    return (doc or {}).get("content", "")


def _write_overview(client, project_id: str, content: str) -> None:
    written = client.put(
        _overview(client, project_id),
        json={"content": content, "expected_version": 0},
        headers=session_auth_headers("user-1"),
    )
    assert written.status_code == 200, written.text


def test_writing_memory_through_this_endpoint_is_retired_and_says_where(client):
    """写记忆的旧入口整个停用了，而且明说停在哪、该去哪写，不是静默吞掉。

    `scope="everyone"` 那一路先停：它把总览文档写成了一份只增不减的观察清单
    （话题「记忆机制照搬CC」）。剩下的写入面随后一起收掉——`cheese_remember` 写
    的是条目池，而条目池已经不再注入任何地方，所以「已记入」是一句谎话。旧会话
    里的调用方读到的这句要说清记忆现在是什么形状：`~/.cheese/memory/` 下的
    文件。总览文档只写「项目是什么」。
    """
    project_id, topic_id = _project_and_room(client)

    for body in (
        {"content": FACT, "topic": topic_id, "scope": "everyone"},
        {"content": FACT, "scope": "everyone"},
        {"content": FACT, "topic": topic_id},
        {"content": FACT},
    ):
        refused = client.post(f"/projects/{project_id}/memory", json=body)
        assert refused.status_code == 422, refused.text
        assert "停用" in refused.text
        assert "~/.cheese/memory/" in refused.text
    assert FACT not in _overview_text(client, project_id)


def test_another_room_reads_the_overview_document_on_its_next_turn(client, stub_hooks):
    """别的房间跑一轮，总览那一份在提示词里。"""
    project_id, topic_id = _project_and_room(client)
    _write_overview(client, project_id, FACT)

    _say(client, topic_id)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert FACT in prompt


def test_another_room_gets_only_what_the_project_is(client, stub_hooks):
    """每段对话只注入「项目是什么」：手抄进别的小节的字谁都读不到，写在那儿
    等于没写——不必每轮往每段对话塞项目快照。"""
    project_id, topic_id = _project_and_room(client)
    _write_overview(
        client,
        project_id,
        "## 项目是什么\n\n给高中生做算法课。\n\n"
        "## 现在在做什么\n\n- 这一条是手抄的，不算数。\n",
    )

    _say(client, topic_id)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert "给高中生做算法课" in prompt
    assert "## 现在在做什么" not in prompt
    assert "这一条是手抄的" not in prompt


def test_a_stranger_cannot_read_the_overview(client):
    """总览是项目的：认得出来是谁，还得是项目里的人。"""
    project_id, _ = _project_and_room(client)
    stranger = session_auth_headers("stranger")
    resp = client.get(f"/projects/{project_id}/overview", headers=stranger)
    assert resp.status_code == 403, resp.text
    doc = overview_of(client, project_id, headers=session_auth_headers("user-1"))
    assert client.get(f"/documents/{doc}", headers=stranger).status_code == 403
