"""Probe a provider against the Anthropic-protocol checklist.

(话题《credits 额度设计》§7.1.2 阶段一)

Vendor docs are vague or wrong about the things that actually break a coding
Agent — cache_control support, whether `usage` splits the four token classes,
and what the gateway does with a claude-* model name. This script answers those
by making real requests, so a candidate is judged on behaviour, not marketing.

Stdlib only, so it runs without the backend venv:

    python backend/scripts/probe_provider.py \
        --base-url https://open.bigmodel.cn/api/anthropic \
        --key "$KEY" --model glm-5.2 --haiku-model glm-4.5-air \
        --openai-base-url https://open.bigmodel.cn/api/paas/v4 \
        --embedding-model embedding-3

Exit code is 1 if any BLOCKER check fails — a blocker means the Agent cannot run
at all, not that it runs worse.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 120

# Checks that disqualify a candidate outright rather than merely degrade it.
BLOCKERS = {1, 2, 3, 6, 7}


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str, str]] = []

    def add(self, num: int, name: str, status: str, detail: str = "") -> None:
        self.rows.append((num, name, status, detail))
        mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "SKIP": "— "}[status]
        blocker = " [BLOCKER]" if num in BLOCKERS and status == "FAIL" else ""
        print(f"{mark} {num:>2}. {name}{blocker}")
        if detail:
            for line in detail.splitlines():
                print(f"       {line}")

    def failed_blockers(self) -> list[int]:
        return [n for n, _, s, _ in self.rows if s == "FAIL" and n in BLOCKERS]


def post(url: str, key: str, payload: dict, *, anthropic: bool, stream: bool = False):
    """POST JSON. Returns (status, headers, body_or_line_iter)."""
    headers = {"content-type": "application/json"}
    if anthropic:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        headers["authorization"] = f"Bearer {key}"  # gateways differ on which they read
    else:
        headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode(errors="replace")
    if stream:
        return resp.status, dict(resp.headers), resp
    return resp.status, dict(resp.headers), resp.read().decode(errors="replace")


def messages_url(base: str) -> str:
    return base.rstrip("/") + "/v1/messages"


WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


def check_basic(r: Result, base: str, key: str, model: str) -> dict | None:
    """1. Anthropic Messages endpoint exists and answers."""
    status, _, body = post(
        messages_url(base),
        key,
        {
            "model": model,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        },
        anthropic=True,
    )
    if status != 200:
        r.add(1, "Anthropic Messages 端点可用", "FAIL", f"HTTP {status}: {body[:300]}")
        return None
    data = json.loads(body)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    r.add(1, "Anthropic Messages 端点可用", "PASS", f"回复: {text.strip()[:60]}")
    return data


def check_tool_use(r: Result, base: str, key: str, model: str) -> None:
    """2. tools + tool_result round trip — the Agent dies without this."""
    first = {
        "model": model,
        "max_tokens": 512,
        "tools": [WEATHER_TOOL],
        "messages": [
            {
                "role": "user",
                "content": "What is the weather in Hangzhou? Use the tool.",
            }
        ],
    }
    status, _, body = post(messages_url(base), key, first, anthropic=True)
    if status != 200:
        r.add(2, "tool_use 多轮往返", "FAIL", f"第一轮 HTTP {status}: {body[:300]}")
        return
    data = json.loads(body)
    calls = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
    if not calls:
        r.add(
            2,
            "tool_use 多轮往返",
            "FAIL",
            f"模型没有发起工具调用 (stop_reason={data.get('stop_reason')})",
        )
        return
    call = calls[0]
    # Feed the result back — this second leg is where loose implementations break.
    second = dict(first)
    second["messages"] = [
        first["messages"][0],
        {"role": "assistant", "content": data["content"]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": "18°C, clear.",
                }
            ],
        },
    ]
    status2, _, body2 = post(messages_url(base), key, second, anthropic=True)
    if status2 != 200:
        r.add(
            2,
            "tool_use 多轮往返",
            "FAIL",
            f"第二轮(tool_result) HTTP {status2}: {body2[:300]}",
        )
        return
    final = "".join(b.get("text", "") for b in json.loads(body2).get("content", []))
    r.add(
        2,
        "tool_use 多轮往返",
        "PASS",
        f"工具={call.get('name')} 终答={final.strip()[:60]}",
    )


def check_stream(r: Result, base: str, key: str, model: str) -> None:
    """3. tool_use blocks arrive correctly as streaming deltas."""
    payload = {
        "model": model,
        "max_tokens": 512,
        "stream": True,
        "tools": [WEATHER_TOOL],
        "messages": [{"role": "user", "content": "Weather in Beijing? Use the tool."}],
    }
    status, _, resp = post(
        messages_url(base), key, payload, anthropic=True, stream=True
    )
    if status != 200:
        r.add(3, "流式 tool_use 增量", "FAIL", f"HTTP {status}")
        return
    events, saw_tool_start, saw_json_delta = set(), False, False
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        events.add(ev.get("type", ""))
        if ev.get("type") == "content_block_start":
            if ev.get("content_block", {}).get("type") == "tool_use":
                saw_tool_start = True
        if ev.get("type") == "content_block_delta":
            if ev.get("delta", {}).get("type") == "input_json_delta":
                saw_json_delta = True
    if saw_tool_start and saw_json_delta:
        r.add(3, "流式 tool_use 增量", "PASS", f"事件类型: {sorted(events)}")
    else:
        r.add(
            3,
            "流式 tool_use 增量",
            "FAIL",
            f"缺少 tool_use 增量 (block_start={saw_tool_start}, "
            f"input_json_delta={saw_json_delta}); 事件: {sorted(events)}",
        )


def _long_prefix() -> str:
    # Caching has a minimum-token floor; make the prefix comfortably long.
    return (
        "这是一段用于测试前缀缓存的公共上下文，模拟教学场景下全班共享的题面与起始代码。"
        * 120
    )


def check_cache(r: Result, base: str, key: str, model: str) -> None:
    """4 & 5. cache_control accepted, and does `usage` split the four token classes?

    Sent twice: the second call is what would report a cache read.
    """
    payload = {
        "model": model,
        "max_tokens": 32,
        "system": [
            {
                "type": "text",
                "text": _long_prefix(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "Reply with: ok"}],
    }
    status, _, body = post(messages_url(base), key, payload, anthropic=True)
    if status != 200:
        r.add(4, "cache_control 被接受", "FAIL", f"HTTP {status}: {body[:300]}")
        r.add(5, "usage 四类 token 分列", "SKIP", "依赖第 4 项")
        return
    r.add(4, "cache_control 被接受", "PASS", "请求未因 cache_control 报错")

    status2, _, body2 = post(messages_url(base), key, payload, anthropic=True)
    usage = json.loads(body2 if status2 == 200 else body).get("usage", {})
    wanted = [
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ]
    missing = [k for k in wanted if k not in usage]
    detail = f"usage = {json.dumps(usage, ensure_ascii=False)}"
    if missing:
        r.add(5, "usage 四类 token 分列", "FAIL", f"缺失字段: {missing}\n{detail}")
    elif usage.get("cache_read_input_tokens", 0) > 0:
        r.add(5, "usage 四类 token 分列", "PASS", f"第二次命中缓存\n{detail}")
    else:
        r.add(
            5,
            "usage 四类 token 分列",
            "WARN",
            f"字段齐全但第二次未命中缓存（可能有最小 token 门槛或需预热）\n{detail}",
        )


def check_alias_and_hijack(
    r: Result, base: str, key: str, model: str, haiku: str
) -> None:
    """6. our explicit model names pass through; 7. what happens to a claude-* name.

    [1211]: an unmapped alias reaches the gateway as a claude-* name. Whether that
    400s (fatal) or silently maps (survivable) decides how safe the swap is.
    """
    ok = []
    for name in filter(None, [model, haiku]):
        status, _, body = post(
            messages_url(base),
            key,
            {
                "model": name,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            },
            anthropic=True,
        )
        ok.append((name, status, "" if status == 200 else body[:160]))
    bad = [x for x in ok if x[1] != 200]
    if bad:
        r.add(
            6,
            "显式模型名可用",
            "FAIL",
            "\n".join(f"{n}: HTTP {s} {b}" for n, s, b in bad),
        )
    else:
        r.add(6, "显式模型名可用", "PASS", ", ".join(f"{n} ✓" for n, _, _ in ok))

    probe = "claude-sonnet-4-5"
    status, _, body = post(
        messages_url(base),
        key,
        {
            "model": probe,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        anthropic=True,
    )
    if status == 200:
        r.add(
            7,
            "claude-* 模型名不致命",
            "PASS",
            f"{probe} 被静默映射（HTTP 200）——漏映射别名不会打挂整轮",
        )
    else:
        r.add(
            7,
            "claude-* 模型名不致命",
            "WARN",
            f"{probe} → HTTP {status}: {body[:200]}\n"
            f"即 [1211] 的失败形态：三档别名必须全部显式映射，漏一个就 400",
        )


def check_openai_side(r: Result, base: str, key: str, model: str, emb: str) -> None:
    """8. OpenViking 需要的 OpenAI 协议链路：chat + embeddings。"""
    if not base:
        r.add(8, "OpenAI 协议端点 + embedding", "SKIP", "未提供 --openai-base-url")
        return
    root = base.rstrip("/")
    status, _, body = post(
        root + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        anthropic=False,
    )
    chat_ok = status == 200
    if not emb:
        r.add(
            8,
            "OpenAI 协议端点 + embedding",
            "FAIL" if not chat_ok else "WARN",
            f"chat/completions HTTP {status}；未提供 --embedding-model，"
            f"embedding 未验证（该厂商若无 embedding 模型，OpenViking 需另配一家）",
        )
        return
    status2, _, body2 = post(
        root + "/embeddings", key, {"model": emb, "input": "hello"}, anthropic=False
    )
    if chat_ok and status2 == 200:
        dim = len(json.loads(body2)["data"][0]["embedding"])
        r.add(
            8, "OpenAI 协议端点 + embedding", "PASS", f"chat ✓, embedding ✓ (dim={dim})"
        )
    else:
        r.add(
            8,
            "OpenAI 协议端点 + embedding",
            "FAIL",
            f"chat HTTP {status} {'' if chat_ok else body[:160]}\n"
            f"embeddings HTTP {status2} {'' if status2 == 200 else body2[:160]}",
        )


def check_limits(r: Result, base: str, key: str, model: str) -> None:
    """9-11. 上下文上限 / 速率限制 / 计费可对账 —— 部分只能看响应头与文档。"""
    status, headers, body = post(
        messages_url(base),
        key,
        {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        anthropic=True,
    )
    rate = {
        k: v
        for k, v in headers.items()
        if "ratelimit" in k.lower() or "x-request-id" == k.lower()
    }
    r.add(
        10,
        "速率限制信息可见",
        "PASS" if rate else "WARN",
        json.dumps(rate, ensure_ascii=False)
        if rate
        else "响应头未暴露限流信息，需查文档或问厂商",
    )
    usage = json.loads(body).get("usage", {}) if status == 200 else {}
    r.add(
        11,
        "计费可对账（usage 随响应返回）",
        "PASS" if usage else "FAIL",
        json.dumps(usage, ensure_ascii=False),
    )
    r.add(9, "上下文长度上限", "SKIP", "查厂商文档填写；编程 Agent 负载对此敏感")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-url",
        required=True,
        help="Anthropic 兼容端点，如 https://open.bigmodel.cn/api/anthropic",
    )
    p.add_argument("--key", required=True)
    p.add_argument("--model", required=True, help="主模型（opus/sonnet 档）")
    p.add_argument("--haiku-model", default="", help="轻量档模型")
    p.add_argument(
        "--openai-base-url", default="", help="OpenAI 兼容端点（OpenViking 用）"
    )
    p.add_argument(
        "--openai-model", default="", help="OpenAI 侧 chat 模型，默认同 --model"
    )
    p.add_argument(
        "--embedding-model", default="", help="embedding 模型，如 embedding-3"
    )
    a = p.parse_args()

    print(f"\n探测目标: {a.base_url}  模型: {a.model}\n" + "=" * 64)
    r = Result()
    if check_basic(r, a.base_url, a.key, a.model) is None:
        print("\n端点不可用，后续检查跳过。")
        return 1
    check_tool_use(r, a.base_url, a.key, a.model)
    check_stream(r, a.base_url, a.key, a.model)
    check_cache(r, a.base_url, a.key, a.model)
    check_alias_and_hijack(r, a.base_url, a.key, a.model, a.haiku_model)
    check_openai_side(
        r, a.openai_base_url, a.key, a.openai_model or a.model, a.embedding_model
    )
    check_limits(r, a.base_url, a.key, a.model)

    print("=" * 64)
    blockers = r.failed_blockers()
    if blockers:
        print(f"结论: 不通过 —— BLOCKER 项失败: {blockers}")
        return 1
    warns = [n for n, _, s, _ in r.rows if s in ("FAIL", "WARN")]
    print(f"结论: 通过阶段一{'（有降级项: ' + str(warns) + '）' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
