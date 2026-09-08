"""私聊的执行路：一次不落地的模型调用——没有机器，也没有工具。

一轮活的默认形态是「在一台机器上开一个 Claude Code 会话」，因为一轮活要读文件、
跑命令、开 PR。私聊不做这些事：它是一段对话。而机器是**按话题**分配的，所以每个
人的私聊都在替一段对话占着一台机器（配了 Cloud 的部署上就是一台云主机）。

所以私聊换成这一条：后端自己向模型问一次，把回答当成这一轮的最终结果交回去。代价
是明确的、也是故意的——**私聊里的芝士没有任何工具**，读不了文件、跑不了命令、也
用不了 cheese 命令。要它干活，去开话题。

走的是 Anthropic 协议（``/v1/messages``），因为平台的模型出口本来就是这个协议
（``settings.anthropic_base_url`` 后面挂的网关），凭据也是同一份项目虚拟 key。换句
话说这条路和沙箱那条路计费在同一个口子上，只是不占机器。
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# 一次纯对话该在几秒内回来。比一轮活的上限短得多是对的：这条路上没有工具调用，
# 只有一次生成，久到分钟级说明出口出问题了，而不是它在想。
_TIMEOUT_S = 90.0
# 单次回答的上限。私聊是对话，不是文档生成。
_MAX_TOKENS = 2048


class PlainChatUnavailable(RuntimeError):
    """这条路走不通（没有出口、没有凭据、或者出口报错）。

    单独一个类型而不是让 httpx 的异常往上跑：调用方要把它变成房间里的一句人话，
    而「连接被拒绝」不是人话。
    """


@dataclass(frozen=True)
class PlainReply:
    text: str
    input_tokens: int
    output_tokens: int


def route_from_env(env: dict[str, str] | None) -> tuple[str, str] | None:
    """从一轮活的模型环境里取出 (base_url, api_key)，取不到就 None。

    读的是 ``_model_kwargs`` 已经组装好的那一份，而不是自己再去拼一遍设置——沙箱
    里的 claude 读哪一份，这里就读哪一份，两条路的出口和凭据因此不可能分家。

    订阅那条路在这里必然返回 None：它的凭据是机器上的 OAuth token，后端手里根本
    没有，这是事实而不是缺陷。调用方据此退回去说清楚，而不是拿一个空 Bearer 去撞
    401。
    """
    if not env:
        return None
    base = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    key = (env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not base or not key:
        return None
    return base.rstrip("/"), key


async def ask(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> PlainReply:
    """问一次，拿回一段文本。

    不流式：这条路上没有工具事件、没有中间过程，流式唯一能买到的是打字机效果，而
    它要换来一整套增量帧的拼装和失败处理。私聊的回答是一段话，等它写完再显示。

    ``transport`` 只为测试留（同 ``GitHubAppTokens``）：这条路上唯一值得测的就是
    「出口这样答的时候，房间里会出现什么」，而那必须不真的打出去。
    """
    if not messages:
        raise PlainChatUnavailable("没有可发送的消息")
    payload = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "system": system_prompt,
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=transport) as client:
            resp = await client.post(
                f"{base_url}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": api_key,
                    "authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise PlainChatUnavailable(f"模型出口没有响应：{exc}") from exc
    if resp.status_code >= 400:
        # 出口自己的话原样带上去：私聊没有会话日志可查，这一句就是全部现场。
        detail = resp.text.strip()[:300] or f"HTTP {resp.status_code}"
        raise PlainChatUnavailable(f"模型出口返回 {resp.status_code}：{detail}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise PlainChatUnavailable("模型出口返回的不是 JSON") from exc
    text = "".join(
        part.get("text", "")
        for part in body.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if not text:
        raise PlainChatUnavailable("模型没有返回任何文字")
    usage = body.get("usage") or {}
    return PlainReply(
        text=text,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )
