"""Probe model providers against the Anthropic-protocol checklist.

(话题《credits 额度设计》§7.1.2 阶段一)

Vendor docs are vague or wrong about the things that actually break a coding
Agent — whether cache_control does anything, whether `usage` splits the four
token classes, what the gateway does with a claude-* model name. This script
answers those by making real requests, so a candidate is judged on behaviour.

Gateway and model are independent axes: 百炼 serves qwen, glm, deepseek and
kimi through one Anthropic endpoint, so "which gateway" and "which model" can
be varied separately. Run several targets in one pass to compare them — with a
model held constant across gateways, the diff isolates the gateway.

    python backend/scripts/probe_provider.py --config tmp/probe_targets.json

Single target, no config file:

    python backend/scripts/probe_provider.py \
        --base-url https://open.bigmodel.cn/api/anthropic \
        --key-file tmp/glm.key --model glm-5.2 --haiku-model glm-4.5-air \
        --openai-base-url https://open.bigmodel.cn/api/paas/v4 \
        --embedding-model embedding-3

Keys come from files (`key_file`) or env (`key_env`) so they stay out of argv,
shell history and transcripts. Stdlib only — no backend venv needed.

Exit code 1 if any target fails a BLOCKER check; a blocker means the Agent
cannot run at all, not that it runs worse.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 120

# Checks that disqualify a candidate outright rather than merely degrade it.
BLOCKERS = {1, 2, 3, 6}

CHECK_NAMES = {
    1: "Anthropic Messages 端点",
    2: "tool_use 多轮往返",
    3: "流式 tool_use 增量",
    4: "cache_control 被接受",
    5: "usage 四类 token 分列",
    6: "显式模型名可用",
    7: "claude-* 模型名不致命",
    8: "OpenAI 端点 + embedding",
    9: "上下文长度上限",
    10: "速率限制信息可见",
    11: "计费可对账",
    12: "隐式缓存（无标记复用）",
}

MARK = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "—"}

USAGE_FIELDS = [
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
]


class Result:
    """Collects one target's rows plus the metrics the comparison table needs."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[int, tuple[str, str]] = {}
        self.metrics: dict[str, object] = {}

    def add(self, num: int, status: str, detail: str = "") -> None:
        self.rows[num] = (status, detail)
        blocker = " [BLOCKER]" if num in BLOCKERS and status == "FAIL" else ""
        print(f"  {MARK[status]} {num:>2}. {CHECK_NAMES[num]}{blocker}")
        for line in detail.splitlines():
            print(f"        {line}")

    def status(self, num: int) -> str:
        return self.rows.get(num, ("SKIP", ""))[0]

    def failed_blockers(self) -> list[int]:
        return [n for n, (s, _) in self.rows.items() if s == "FAIL" and n in BLOCKERS]


def post(url: str, key: str, payload: dict, *, anthropic: bool, stream: bool = False):
    """POST JSON. Returns (status, headers, body_or_response_stream)."""
    headers = {"content-type": "application/json"}
    headers["authorization"] = f"Bearer {key}"
    if anthropic:
        # Gateways disagree on which auth header they read; send both.
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode(errors="replace")
    except urllib.error.URLError as exc:
        return 0, {}, f"连接失败: {exc.reason}"
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


def _long_prefix(tag: str) -> str:
    """A shared prefix like a class assignment's brief + starter code.

    Explicit caching has a 1k-token floor, so keep it comfortably long. The tag
    keeps the explicit and implicit probes from colliding in the same cache.
    """
    line = f"[{tag}] 用于测试前缀缓存的公共上下文，模拟全班共享的题面与起始代码。"
    return line * 120


def check_basic(r: Result, t: dict, key: str) -> bool:
    status, _, body = post(
        messages_url(t["base_url"]),
        key,
        {
            "model": t["model"],
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with the word: ok"}],
        },
        anthropic=True,
    )
    if status != 200:
        r.add(1, "FAIL", f"HTTP {status}: {body[:300]}")
        return False
    text = "".join(b.get("text", "") for b in json.loads(body).get("content", []))
    r.add(1, "PASS", f"回复: {text.strip()[:60]}")
    return True


def check_tool_use(r: Result, t: dict, key: str) -> None:
    """The Agent dies without this, so both legs of the round trip are tested."""
    first = {
        "model": t["model"],
        "max_tokens": 512,
        "tools": [WEATHER_TOOL],
        "messages": [
            {
                "role": "user",
                "content": "What is the weather in Hangzhou? Use the tool.",
            }
        ],
    }
    status, _, body = post(messages_url(t["base_url"]), key, first, anthropic=True)
    if status != 200:
        r.add(2, "FAIL", f"第一轮 HTTP {status}: {body[:300]}")
        return
    data = json.loads(body)
    calls = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
    if not calls:
        r.add(2, "FAIL", f"模型未发起工具调用 (stop_reason={data.get('stop_reason')})")
        return
    call = calls[0]
    # Feeding the result back is where loose implementations break.
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
    status2, _, body2 = post(messages_url(t["base_url"]), key, second, anthropic=True)
    if status2 != 200:
        r.add(2, "FAIL", f"第二轮(tool_result) HTTP {status2}: {body2[:300]}")
        return
    final = "".join(b.get("text", "") for b in json.loads(body2).get("content", []))
    r.add(2, "PASS", f"工具={call.get('name')} 终答={final.strip()[:50]}")


def check_stream(r: Result, t: dict, key: str) -> None:
    """Streaming deltas, and where in the stream `usage` becomes complete.

    百炼 documents `message_start` carrying only two usage fields with the full
    four arriving in `message_delta` — metering must read the right event.
    """
    payload = {
        "model": t["model"],
        "max_tokens": 512,
        "stream": True,
        "tools": [WEATHER_TOOL],
        "messages": [{"role": "user", "content": "Weather in Beijing? Use the tool."}],
    }
    status, _, resp = post(
        messages_url(t["base_url"]), key, payload, anthropic=True, stream=True
    )
    if status != 200:
        r.add(3, "FAIL", f"HTTP {status}")
        return
    events: set[str] = set()
    saw_start = saw_delta = False
    usage_by_event: dict[str, list[str]] = {}
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        kind = ev.get("type", "")
        events.add(kind)
        if kind == "content_block_start":
            saw_start |= ev.get("content_block", {}).get("type") == "tool_use"
        if kind == "content_block_delta":
            saw_delta |= ev.get("delta", {}).get("type") == "input_json_delta"
        usage = ev.get("usage") or ev.get("message", {}).get("usage")
        if usage:
            usage_by_event[kind] = sorted(usage.keys())
    r.metrics["stream_usage"] = usage_by_event
    where = "; ".join(f"{k}={v}" for k, v in usage_by_event.items()) or "无 usage"
    if saw_start and saw_delta:
        r.add(3, "PASS", f"usage 出现在: {where}")
    else:
        r.add(
            3,
            "FAIL",
            f"缺 tool_use 增量 (block_start={saw_start}, json_delta={saw_delta})\n"
            f"事件: {sorted(events)}",
        )


def _cache_payload(t: dict, tag: str, *, explicit: bool) -> dict:
    block: dict = {"type": "text", "text": _long_prefix(tag)}
    if explicit:
        block["cache_control"] = {"type": "ephemeral"}
    return {
        "model": t["model"],
        "max_tokens": 32,
        "system": [block],
        "messages": [{"role": "user", "content": "Reply with: ok"}],
    }


def check_cache(r: Result, t: dict, key: str) -> None:
    """4/5: explicit cache accepted, and does the second call report a read?"""
    url = messages_url(t["base_url"])
    payload = _cache_payload(t, "explicit", explicit=True)
    status, _, body = post(url, key, payload, anthropic=True)
    if status != 200:
        r.add(4, "FAIL", f"HTTP {status}: {body[:300]}")
        r.add(5, "SKIP", "依赖第 4 项")
        return
    r.add(4, "PASS", "请求未因 cache_control 报错")

    status2, _, body2 = post(url, key, payload, anthropic=True)
    usage = json.loads(body2 if status2 == 200 else body).get("usage", {})
    r.metrics["usage"] = usage
    read = usage.get("cache_read_input_tokens", 0)
    r.metrics["explicit_cache_read"] = read
    missing = [k for k in USAGE_FIELDS if k not in usage]
    detail = f"usage = {json.dumps(usage, ensure_ascii=False)}"
    if missing:
        r.add(5, "FAIL", f"缺失字段: {missing}\n{detail}")
    elif read > 0:
        r.add(5, "PASS", f"第二次命中缓存 {read} tokens\n{detail}")
    else:
        r.add(5, "WARN", f"字段齐全但未命中（可能有 token 门槛或需预热）\n{detail}")


def check_implicit_cache(r: Result, t: dict, key: str) -> None:
    """12: some gateways cache automatically with no cache_control at all.

    Worth separating: implicit caching is cheaper than nothing but typically a
    smaller discount than explicit, and on some gateways it cannot be disabled.
    """
    url = messages_url(t["base_url"])
    payload = _cache_payload(t, "implicit", explicit=False)
    status, _, _ = post(url, key, payload, anthropic=True)
    if status != 200:
        r.add(12, "SKIP", f"首次请求 HTTP {status}")
        return
    status2, _, body2 = post(url, key, payload, anthropic=True)
    if status2 != 200:
        r.add(12, "SKIP", f"第二次请求 HTTP {status2}")
        return
    usage = json.loads(body2).get("usage", {})
    read = usage.get("cache_read_input_tokens", 0)
    r.metrics["implicit_cache_read"] = read
    if read > 0:
        r.add(12, "PASS", f"无标记也命中 {read} tokens（隐式缓存开启）")
    else:
        r.add(12, "WARN", f"未命中，需显式标记才有缓存\nusage = {usage}")


def check_models(r: Result, t: dict, key: str) -> None:
    """6: our configured names work. 7: what a claude-* name does.

    [1211]: an unmapped alias reaches the gateway as a claude-* name. Whether
    that 400s or is silently mapped decides how dangerous a missed alias is.
    """
    url = messages_url(t["base_url"])
    tried = []
    for name in filter(None, [t["model"], t.get("haiku_model")]):
        status, _, body = post(
            url,
            key,
            {
                "model": name,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            },
            anthropic=True,
        )
        tried.append((name, status, "" if status == 200 else body[:140]))
    bad = [x for x in tried if x[1] != 200]
    if bad:
        r.add(6, "FAIL", "\n".join(f"{n}: HTTP {s} {b}" for n, s, b in bad))
    else:
        r.add(6, "PASS", ", ".join(f"{n} ✓" for n, _, _ in tried))

    probe = "claude-sonnet-4-5"
    status, _, body = post(
        url,
        key,
        {
            "model": probe,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        anthropic=True,
    )
    r.metrics["claude_alias_status"] = status
    if status == 200:
        r.add(7, "PASS", f"{probe} 被静默映射——漏映射别名不会打挂整轮")
    else:
        r.add(
            7,
            "WARN",
            f"{probe} → HTTP {status}: {body[:160]}\n"
            f"[1211] 的失败形态：三档别名必须全部显式映射，漏一个就 400",
        )


def check_openai_side(r: Result, t: dict, key: str) -> None:
    """8: the OpenAI-protocol leg OpenViking needs — chat plus embeddings."""
    base = t.get("openai_base_url")
    if not base:
        r.add(8, "SKIP", "未配置 openai_base_url")
        return
    root = base.rstrip("/")
    model = t.get("openai_model") or t["model"]
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
    emb = t.get("embedding_model")
    if not emb:
        r.add(
            8,
            "WARN" if chat_ok else "FAIL",
            f"chat/completions HTTP {status}；未配置 embedding_model。"
            f"该厂商若无 embedding，OpenViking 需另配一家",
        )
        return
    status2, _, body2 = post(
        root + "/embeddings", key, {"model": emb, "input": "hello"}, anthropic=False
    )
    if chat_ok and status2 == 200:
        dim = len(json.loads(body2)["data"][0]["embedding"])
        r.metrics["embedding_dim"] = dim
        r.add(8, "PASS", f"chat ✓, embedding ✓ (dim={dim})")
    else:
        r.add(
            8,
            "FAIL",
            f"chat HTTP {status} {'' if chat_ok else body[:140]}\n"
            f"embeddings HTTP {status2} {'' if status2 == 200 else body2[:140]}",
        )


def check_limits(r: Result, t: dict, key: str) -> None:
    """9-11: context ceiling, rate-limit visibility, per-response usage."""
    status, headers, body = post(
        messages_url(t["base_url"]),
        key,
        {
            "model": t["model"],
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        anthropic=True,
    )
    rate = {
        k: v
        for k, v in headers.items()
        if "ratelimit" in k.lower() or k.lower() == "x-request-id"
    }
    r.add(
        10,
        "PASS" if rate else "WARN",
        json.dumps(rate, ensure_ascii=False) if rate else "响应头未暴露限流信息",
    )
    usage = json.loads(body).get("usage", {}) if status == 200 else {}
    r.add(11, "PASS" if usage else "FAIL", json.dumps(usage, ensure_ascii=False))
    r.add(9, "SKIP", "查厂商文档填写；编程 Agent 负载对此敏感")


def resolve_key(t: dict) -> str:
    """Read the key from a file or env var; inline keys are a last resort."""
    if t.get("key_file"):
        return Path(t["key_file"]).read_text(encoding="utf-8").strip()
    if t.get("key_env"):
        val = os.environ.get(t["key_env"], "")
        if not val:
            raise SystemExit(f"环境变量 {t['key_env']} 为空")
        return val
    if t.get("key"):
        return t["key"]
    raise SystemExit(f"目标 {t.get('name')} 未提供 key_file / key_env / key")


def run_target(t: dict) -> Result:
    name = t.get("name") or f"{t['base_url']} / {t['model']}"
    r = Result(name)
    print(f"\n{'=' * 70}\n▶ {name}\n  {t['base_url']}  模型: {t['model']}\n")
    key = resolve_key(t)
    if not check_basic(r, t, key):
        print("  端点不可用，跳过其余检查。")
        return r
    check_tool_use(r, t, key)
    check_stream(r, t, key)
    check_cache(r, t, key)
    check_implicit_cache(r, t, key)
    check_models(r, t, key)
    check_openai_side(r, t, key)
    check_limits(r, t, key)
    return r


def render_matrix(results: list[Result]) -> None:
    """Side-by-side matrix — the point of running several targets in one pass."""
    print(f"\n{'=' * 70}\n对比矩阵\n")
    width = max(len(CHECK_NAMES[n]) for n in CHECK_NAMES) + 2
    header = "检查项".ljust(width - 4) + "".join(
        f"  {r.name[:16]:<16}" for r in results
    )
    print(header)
    print("-" * len(header))
    for num in sorted(CHECK_NAMES):
        row = f"{num:>2}. {CHECK_NAMES[num]}".ljust(width)
        for r in results:
            row += f"  {MARK[r.status(num)]:<16}"
        print(row)

    print("\n关键指标")
    print("-" * len(header))
    keys = [
        ("explicit_cache_read", "显式缓存命中 tokens"),
        ("implicit_cache_read", "隐式缓存命中 tokens"),
        ("claude_alias_status", "claude-* 返回码"),
        ("embedding_dim", "embedding 维度"),
    ]
    for mkey, label in keys:
        row = label.ljust(width)
        for r in results:
            row += f"  {str(r.metrics.get(mkey, '—')):<16}"
        print(row)


def load_targets(a: argparse.Namespace) -> list[dict]:
    if a.config:
        data = json.loads(Path(a.config).read_text(encoding="utf-8"))
        return data["targets"] if isinstance(data, dict) else data
    if not (a.base_url and a.model):
        raise SystemExit("需要 --config，或同时给出 --base-url 与 --model")
    return [
        {
            "name": a.name or a.model,
            "base_url": a.base_url,
            "model": a.model,
            "haiku_model": a.haiku_model,
            "key": a.key,
            "key_file": a.key_file,
            "key_env": a.key_env,
            "openai_base_url": a.openai_base_url,
            "openai_model": a.openai_model,
            "embedding_model": a.embedding_model,
        }
    ]


def main() -> int:
    p = argparse.ArgumentParser(
        description="探测模型厂商的 Anthropic 协议兼容性（可多目标对比）"
    )
    p.add_argument("--config", help="JSON 配置，含 targets 列表；多目标对比用这个")
    p.add_argument("--name", default="", help="单目标模式下的显示名")
    p.add_argument("--base-url", help="Anthropic 兼容端点")
    p.add_argument("--model", help="主模型（opus/sonnet 档）")
    p.add_argument("--haiku-model", default="", help="轻量档模型")
    p.add_argument("--key", default="", help="不推荐：会进入 shell 历史")
    p.add_argument("--key-file", default="", help="推荐：存放 key 的文件路径")
    p.add_argument("--key-env", default="", help="推荐：存放 key 的环境变量名")
    p.add_argument("--openai-base-url", default="", help="OpenAI 兼容端点")
    p.add_argument("--openai-model", default="", help="OpenAI 侧 chat 模型")
    p.add_argument("--embedding-model", default="", help="embedding 模型")
    p.add_argument("--json-out", default="", help="把完整结果写成 JSON")
    a = p.parse_args()

    targets = load_targets(a)
    results = [run_target(t) for t in targets]

    if len(results) > 1:
        render_matrix(results)

    if a.json_out:
        payload = [
            {
                "name": r.name,
                "checks": {
                    str(n): {"status": s, "detail": d} for n, (s, d) in r.rows.items()
                },
                "metrics": r.metrics,
            }
            for r in results
        ]
        Path(a.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整结果已写入 {a.json_out}")

    print(f"\n{'=' * 70}")
    failed = False
    for r in results:
        blockers = r.failed_blockers()
        if blockers:
            failed = True
            print(f"{r.name}: 不通过 —— BLOCKER 失败 {blockers}")
        else:
            degraded = [n for n, (s, _) in r.rows.items() if s in ("FAIL", "WARN")]
            note = f"（降级项 {sorted(degraded)}）" if degraded else ""
            print(f"{r.name}: 通过阶段一{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
