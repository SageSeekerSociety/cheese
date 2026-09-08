"""私聊那条不占机器的路：出口这样答的时候，房间里会出现什么。

这条路存在的理由是省掉一台机器，所以它没有会话、没有日志、没有现场——出口说了
什么，房间里就只剩那一句。因此这一份钉的全是「话说得对不对」：失败要能读出是哪一
种失败，成功要把用量带回来（不然这条路等于白花钱不记账）。
"""

import httpx
import pytest

from app.domain.agent import plain_chat

pytestmark = pytest.mark.anyio


def _replies(body: dict, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


def _text(status: int, text: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, text=text))


async def _ask(transport: httpx.MockTransport, **over) -> plain_chat.PlainReply:
    kwargs = {
        "base_url": "http://gw:4000",
        "api_key": "sk-test",
        "model": "claude-x",
        "system_prompt": "你是芝士",
        "messages": [{"role": "user", "content": "在吗"}],
        "transport": transport,
    }
    kwargs.update(over)
    return await plain_chat.ask(**kwargs)


class TestRouteFromEnv:
    """凭据从哪儿来：读的是一轮活已经组装好的模型环境，不是自己再拼一遍设置。"""

    def test_gateway_env_gives_a_route(self):
        assert plain_chat.route_from_env(
            {"ANTHROPIC_BASE_URL": "http://gw:4000/", "ANTHROPIC_AUTH_TOKEN": "sk-1"}
        ) == ("http://gw:4000", "sk-1")

    def test_subscription_has_no_backend_credential(self):
        # 走订阅时凭据是机器上的 OAuth token，后端手里只有一个被刻意置空的
        # ANTHROPIC_AUTH_TOKEN。返回 None 是事实，不是故障——调用方据此说人话，
        # 而不是拿一个空 Bearer 去撞 401（那种失败会被读成「模型出问题了」）。
        assert (
            plain_chat.route_from_env(
                {
                    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                    "ANTHROPIC_AUTH_TOKEN": "",
                    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-xxx",
                }
            )
            is None
        )

    def test_no_env_at_all(self):
        assert plain_chat.route_from_env(None) is None
        assert plain_chat.route_from_env({}) is None


class TestAsk:
    async def test_the_answer_and_its_usage_come_back(self):
        reply = await _ask(
            _replies(
                {
                    "content": [
                        {"type": "text", "text": "在的，"},
                        {"type": "text", "text": "怎么了"},
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 5},
                }
            )
        )
        assert reply.text == "在的，怎么了"
        # 用量必须带回来：这条路不占机器，但它照样花钱，不记账就是白花。
        assert (reply.input_tokens, reply.output_tokens) == (12, 5)

    async def test_non_text_parts_are_dropped_not_rendered(self):
        reply = await _ask(
            _replies(
                {
                    "content": [
                        {"type": "thinking", "thinking": "先想想"},
                        {"type": "text", "text": "答案"},
                    ],
                    "usage": {},
                }
            )
        )
        assert reply.text == "答案"

    async def test_the_gateways_own_words_survive_a_refusal(self):
        # 私聊没有会话日志可查，出口这句话就是全部现场。吞掉它等于把「余额用尽」
        # 变成「模型出问题了」。
        with pytest.raises(plain_chat.PlainChatUnavailable, match="budget exceeded"):
            await _ask(_text(400, "budget exceeded for key sk-…"))

    async def test_the_status_code_is_in_the_sentence(self):
        with pytest.raises(plain_chat.PlainChatUnavailable, match="429"):
            await _ask(_text(429, "slow down"))

    async def test_a_dead_gateway_is_not_an_httpx_traceback(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(plain_chat.PlainChatUnavailable, match="没有响应"):
            await _ask(httpx.MockTransport(boom))

    async def test_an_empty_answer_is_a_failure_not_an_empty_message(self):
        # 空字符串落进房间就是一条空气泡，看起来像芝士答了但什么也没说。
        with pytest.raises(plain_chat.PlainChatUnavailable, match="没有返回任何文字"):
            await _ask(
                _replies({"content": [{"type": "text", "text": "  "}], "usage": {}})
            )

    async def test_nothing_to_send_is_refused_before_any_call(self):
        called = False

        def seen(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"content": [], "usage": {}})

        with pytest.raises(plain_chat.PlainChatUnavailable):
            await _ask(httpx.MockTransport(seen), messages=[])
        assert called is False

    async def test_it_speaks_the_anthropic_protocol_with_the_key_attached(self):
        seen: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["version"] = request.headers.get("anthropic-version")
            seen["key"] = request.headers.get("x-api-key")
            return httpx.Response(
                200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}}
            )

        await _ask(httpx.MockTransport(capture))
        assert seen["url"] == "http://gw:4000/v1/messages"
        assert seen["version"] == "2023-06-01"
        assert seen["key"] == "sk-test"
