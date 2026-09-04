"""一条支线也要能打开自己的预览。

预览是两条不同的路，一条静态一条运行中，而**同一个 id 走进这两条路时的身份是不同
的**：

- 拿什么给人看，是**地点**的事 —— `cheese artifact` / `cheese serve` 都按地点记，
  一条支线预览的是它自己那份结果，房间的还是房间的；
- 谁能看，是**房间**的事 —— 花名册只有房间有一份。

`proxy.may_view_topic` 找花名册的办法是先查一行 `topics`，所以把支线的 id 递给它
不是 403 而是从底下翻上来的 404：活明明在跑、应用明明起着，界面上说这个话题不存在。
房间上永远看不出来，因为那里两个 id 是同一个。

`/{id}/preview` 早就分开了这两个 id，取内容的四条路没跟上：`/app-session`、
`/app/{path}`（HTTP 和 WebSocket 两条）、`/preview/raw`。这里给每一条各钉一次。
"""

import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routes import app_preview
from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers, session_token
from tests.integration.test_app_preview_proxy import EchoingMachine, FakeMachine


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _put_file(project_id: str, place_id: str, name: str, text: str) -> None:
    """把一个文件放进这个地点的工作树 —— `cheese artifact` 点名的就是这种文件。"""
    wt = ws.topic_worktree(uuid.UUID(project_id), uuid.UUID(place_id))
    (wt / name).write_text(text, encoding="utf-8")


# ---- 运行环境预览：握手 --------------------------------------------------------


def test_a_thread_is_handed_the_preview_cookie_instead_of_404(client):
    """握手是打开 iframe 前的第一步。它 404，后面的路一步都走不到。"""
    _pid, room = _room(client)
    thread = _thread(client, room)

    resp = client.get(
        f"/topics/{thread}/app-session", headers=session_auth_headers("alice")
    )

    assert resp.status_code == 200, resp.text
    cookie = resp.headers.get("set-cookie", "")
    assert app_preview.COOKIE_NAME in cookie, cookie
    # 作用域是这条支线自己的路径：同一个房间里两条支线的 cookie 不该互相带过去。
    assert f"Path=/api/topics/{thread}/app" in cookie, cookie


def test_a_stranger_gets_nothing_from_a_threads_handshake(client):
    _pid, room = _room(client)
    thread = _thread(client, room)

    resp = client.get(
        f"/topics/{thread}/app-session", headers=session_auth_headers("mallory")
    )

    assert resp.status_code == 404, resp.text
    assert "set-cookie" not in resp.headers, dict(resp.headers)


# ---- 运行环境预览：HTTP 反代 ---------------------------------------------------


def test_a_thread_reaches_its_own_running_app(client):
    _pid, room = _room(client)
    thread = _thread(client, room)
    token = session_token("alice")
    machine = FakeMachine().attach(uuid.UUID(thread))
    try:
        resp = client.get(f"/topics/{thread}/app/index.html?token={token}")
    finally:
        machine.detach()

    assert resp.status_code == 200, resp.text
    assert resp.content == b"<html><body>hi</body></html>", resp.content
    assert machine.asked == ["GET /index.html"], machine.asked


def test_a_threads_app_is_looked_up_by_the_thread_not_by_its_room(client):
    """支线的活跑在自己的机器会话里，它的隧道也按自己的 id 拨出来。把这个查找折到
    房间上，一条支线就会拿到隔壁的（或者房间自己的）应用 —— 比 404 更糟。"""
    _pid, room = _room(client)
    thread = _thread(client, room)
    token = session_token("alice")
    machine = FakeMachine().attach(uuid.UUID(room))
    try:
        resp = client.get(f"/topics/{thread}/app/index.html?token={token}")
    finally:
        machine.detach()

    assert resp.status_code == 404, resp.text
    assert not machine.asked, "支线的请求被送去了房间的应用"


def test_a_threads_app_is_refused_to_someone_outside_the_room(client):
    """花名册是房间的那一份 —— 支线没有自己的，也不该因此变宽。"""
    _pid, room = _room(client)
    thread = _thread(client, room)
    token = session_token("mallory")
    machine = FakeMachine().attach(uuid.UUID(thread))
    try:
        resp = client.get(f"/topics/{thread}/app/index.html?token={token}")
    finally:
        machine.detach()

    assert resp.status_code == 404, resp.text
    assert not machine.asked, "外人的请求到达了这条支线的应用"


def test_an_id_that_names_nothing_looks_exactly_like_one_that_is_refused(client):
    """两种 404 的正文必须一模一样。id 只是一个 uuid，答得不一样就等于送出一个
    「这个话题存不存在」的探针。"""
    _pid, room = _room(client)
    thread = _thread(client, room)
    stranger = session_token("mallory")

    refused = client.get(f"/topics/{thread}/app/index.html?token={stranger}")
    nonexistent = client.get(f"/topics/{uuid.uuid4()}/app/index.html?token={stranger}")

    assert refused.status_code == nonexistent.status_code == 404
    assert refused.content == nonexistent.content, (refused.content, nonexistent.content)


# ---- 运行环境预览：WebSocket（HMR） -------------------------------------------


def test_a_threads_hmr_socket_reaches_its_own_app(client):
    """开发服务器靠这条推热更新。它连不上，页面就一直重连，看上去和白屏没区别。"""
    _pid, room = _room(client)
    thread = _thread(client, room)
    token = session_token("alice")
    machine = EchoingMachine().attach(uuid.UUID(thread))
    try:
        with client.websocket_connect(
            f"/topics/{thread}/app/@vite/client?token={token}",
            subprotocols=["vite-hmr"],
        ) as socket:
            socket.send_text("ping")
            assert socket.receive_text() == "ping"
    finally:
        machine.detach()

    assert machine.ws_path == "/@vite/client", machine.ws_path


def test_a_threads_hmr_socket_is_refused_to_someone_outside_the_room(client):
    _pid, room = _room(client)
    thread = _thread(client, room)
    token = session_token("mallory")
    machine = EchoingMachine().attach(uuid.UUID(thread))
    try:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/topics/{thread}/app/@vite/client?token={token}"
            ) as socket:
                socket.receive_text()
    finally:
        machine.detach()

    assert machine.ws_path == "", "外人的握手到达了这条支线的应用"


# ---- 静态文件预览：在新窗口打开 ------------------------------------------------


def test_a_thread_opens_its_own_file_artifact_in_a_new_window(client):
    _pid, room = _room(client)
    thread = _thread(client, room)
    _put_file(_pid, thread, "thread.html", "<h1>支线的结果</h1>")
    assert (
        client.post(
            f"/topics/{thread}/artifact", json={"path": "thread.html", "as": "html"}
        ).status_code
        == 200
    )

    resp = client.get(
        f"/topics/{thread}/preview/raw", headers=session_auth_headers("alice")
    )

    assert resp.status_code == 200, resp.text
    assert resp.content == "<h1>支线的结果</h1>".encode()
    # 打开的是一个不透明来源，artifact 里的 JS 不能拿着看的人的身份回头调我们的 API。
    assert resp.headers["content-security-policy"] == "sandbox allow-scripts"


def test_a_threads_new_window_shows_its_own_artifact_not_its_rooms(client):
    """`/preview` 面板早就是按地点算的；「在新窗口打开」是同一份 artifact 的另一种
    打开方式，两者对「这是谁的 artifact」必须给同一个答案。"""
    _pid, room = _room(client)
    thread = _thread(client, room)
    _put_file(_pid, room, "room.html", "<h1>房间的结果</h1>")
    _put_file(_pid, thread, "thread.html", "<h1>支线的结果</h1>")
    client.post(f"/topics/{room}/artifact", json={"path": "room.html", "as": "html"})
    client.post(
        f"/topics/{thread}/artifact", json={"path": "thread.html", "as": "html"}
    )
    headers = session_auth_headers("alice")

    assert client.get(f"/topics/{thread}/preview/raw", headers=headers).content == (
        "<h1>支线的结果</h1>".encode()
    )
    assert client.get(f"/topics/{room}/preview/raw", headers=headers).content == (
        "<h1>房间的结果</h1>".encode()
    )


def test_a_threads_new_window_is_refused_to_someone_outside_the_room(client):
    _pid, room = _room(client)
    thread = _thread(client, room)
    _put_file(_pid, thread, "thread.html", "<h1>支线的结果</h1>")
    client.post(
        f"/topics/{thread}/artifact", json={"path": "thread.html", "as": "html"}
    )

    resp = client.get(
        f"/topics/{thread}/preview/raw", headers=session_auth_headers("mallory")
    )

    assert resp.status_code == 403, resp.text
