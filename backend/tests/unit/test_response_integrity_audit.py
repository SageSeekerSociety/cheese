"""ResponseIntegrityAudit：声明的字节数和真正发出去的字节数对不上就喊出来。

这个中间件是为了回答一个具体问题：浏览器报 ERR_CONTENT_LENGTH_MISMATCH 时，
后端这一侧到底是「整个 body 都发出去了」还是「发到一半连接断了」。`req` 那行
日志是在 handler 返回时打的，body 还没上线，所以它永远显示 200——正是这个盲区
让间歇性截断没法定位。
"""

import pytest

from app.core.obs import ResponseIntegrityAudit


def _only_warning(caplog) -> dict:
    """中间件用 structlog 打的那一条 warning，取它的结构化字段。

    caplog.text 里渲染的是整个 dict（`'declared': 500`），拿字符串匹配既脆又难读，
    所以直接看字段。"""
    warnings = [r.msg for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, warnings
    return warnings[0]


async def _drive(app, path: str = "/x") -> list[dict]:
    """把一个 ASGI app 跑一遍，收集它发出的 message。"""
    scope = {"type": "http", "path": path, "method": "GET", "headers": []}
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await ResponseIntegrityAudit(app)(scope, receive, send)
    return sent


def _responder(declared: int, body: bytes, status: int = 200):
    async def app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-length", str(declared).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


@pytest.mark.anyio
async def test_intact_response_is_silent(caplog) -> None:
    body = b"x" * 128
    sent = await _drive(_responder(len(body), body))

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert "response truncated" not in caplog.text


@pytest.mark.anyio
async def test_short_body_is_reported_with_the_gap(caplog) -> None:
    # 声明 1000 字节，只发出 400——这就是浏览器那侧看到的截断。
    sent = await _drive(_responder(1000, b"y" * 400), path="/topics/t1/blocks")

    assert len(sent) == 2  # 消息照常透传，中间件只观察不拦截
    record = _only_warning(caplog)
    assert record["event"] == "response truncated"
    assert record["path"] == "/topics/t1/blocks"
    assert (record["declared"], record["sent"], record["missing"]) == (1000, 400, 600)


@pytest.mark.anyio
async def test_body_sent_in_chunks_is_summed(caplog) -> None:
    async def app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"30")],
            }
        )
        await send({"type": "http.response.body", "body": b"a" * 10, "more_body": True})
        await send({"type": "http.response.body", "body": b"b" * 20})

    await _drive(app)
    assert "response truncated" not in caplog.text


@pytest.mark.anyio
async def test_abort_after_headers_is_reported(caplog) -> None:
    """连接在写 body 的过程中断掉：这正是「后端发到一半死了」的样子。"""

    async def app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"500")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b"z" * 120, "more_body": True}
        )
        raise ConnectionResetError("peer went away")

    with pytest.raises(ConnectionResetError):
        await _drive(app)

    record = _only_warning(caplog)
    assert record["event"] == "response aborted mid-body"
    assert (record["declared"], record["sent"]) == (500, 120)


@pytest.mark.anyio
async def test_failure_before_headers_is_not_a_truncation(caplog) -> None:
    """还没发响应头就炸了 = 普通的 500，不是截断，别污染这条信号。"""

    async def app(scope, receive, send) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _drive(app)

    assert "response aborted mid-body" not in caplog.text


@pytest.mark.anyio
async def test_streaming_response_without_content_length_is_not_audited(caplog) -> None:
    """SSE / 流式响应没有 Content-Length，没有「声明」可对照，不该误报。"""

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data: hi\n\n"})

    await _drive(app)
    assert "response truncated" not in caplog.text


@pytest.mark.anyio
async def test_non_http_scope_passes_through(caplog) -> None:
    seen: list[str] = []

    async def app(scope, receive, send) -> None:
        seen.append(scope["type"])

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    async def send(message: dict) -> None:
        pass

    await ResponseIntegrityAudit(app)({"type": "websocket"}, receive, send)
    assert seen == ["websocket"]
    assert caplog.text == ""
