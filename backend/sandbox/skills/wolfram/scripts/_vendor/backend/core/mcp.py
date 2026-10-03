"""Wolfram 官方 MCP 客户端 —— 第二层执行与文档检索的唯一通道。

https://agenttools.wolfram.com/mcp 是 Wolfram 托管的 MCP 服务，实测无需任何鉴权，
tools/list 实际暴露三个工具：

    initialize            -> serverInfo {"name":"Wolfram","version":"2026.09.15"}
    WolframLanguageEvaluator("Solve[x^2+3x-4==0,x]")  -> {{x -> -4}, {x -> 1}}   3.6s
    WolframContext("DSolve") -> 0.6s，返回真实文档小节与 reference.wolfram.com 锚点链接
    WolframAlpha("Integrate[x^2 Sin[x],{x,0,Pi}]") -> W|A 的多视图结果
                        （符号解 + 数值近似 + 图像链接 + 黎曼和 + 不定积分，约 780 字）

官方文档（wolfram.com/for-agents）把路由规则写得很明确：所有计算默认用
WolframLanguageEvaluator，需要先查函数名或语法时用 WolframContext。原先自建爬虫 +
HTML 解析 + BM25 干的正是后者，而前者根本无法用爬 HTML 替代（实测 DSolve 只能拿回
「Wolfram|Alpha 页面结果」）。

WolframAlpha 只作 agent 的参考资料（agent 用它看「该取哪几种表示」），它的正文不进
对外答案 —— 否则等于把刚删掉的那个网页档从后门放回来。

**tools/call 的 content 是异构块**：求值结果可能是 text + image 两类。实测
`Plot[x^2 Sin[x], {x,0,Pi}, Filling->Axis]` 返回 `['text', 'image']`，text 只有
`Out[1]= `，真正的图在 image 块里 —— 只拼 text 会把可视化整块丢掉。所以这里返回
ToolOutput 。

手写 JSON-RPC 而不引 MCP SDK：只用得到 initialize / notifications/initialized /
tools/call 三个方法，httpx 本来就装着（openai 的依赖），不值得多一个依赖。

"""

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from backend.core.config import settings

_PROTOCOL = "2025-03-26"
_session_id: str | None = None

#: WL 符号名：字母或 $ 开头，允许数字与 $；排除 \[FormalA] 这类转义形式。
_SYMBOL_RE = re.compile(r"\$?[A-Za-z][A-Za-z0-9$]*\Z")
#: 单次 NameQ 批查的符号个数上限，够小才不会触发服务端输出省略。
_BATCH = 200
#: 官方文档里指向某个符号的链接：paclet:ref/Solve、.../language/ref/Solve.html
_DOC_REF_RE = re.compile(r"(?:paclet:ref/|/language/ref/)([A-Za-z$][A-Za-z0-9$]*)")


def _cache_path() -> Path:
    return Path(settings.ASSET_DIR) / "system_names.json"


def _load_cache() -> dict[str, bool]:
    try:
        loaded = json.loads(_cache_path().read_text(encoding="utf-8"))
        return {k: bool(v) for k, v in loaded.items() if isinstance(k, str)}
    except Exception:
        return {}  # 首次运行或缓存损坏，都当空缓存重来


def _save_cache(cache: dict[str, bool]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        pass  # 落盘失败只影响下次效率，不影响本次结果


def harvest_symbols(text: str) -> int:
    """从 WolframContext 的返回里学出真实符号名，追加进 assets/system_names.json。

    官方文档只会链接真实存在的符号，等于白捡的权威样本：查得越多，judge 的 functions
    信号就越少需要打 MCP 现问（一次 NameQ 批查要好几秒）。只新增不覆盖。返回新增数。
    """
    names = {n for n in _DOC_REF_RE.findall(text or "") if _SYMBOL_RE.match(n)}
    if not names:
        return 0
    cache = _load_cache()
    added = sorted(n for n in names if n not in cache)
    if not added:
        return 0
    cache.update(dict.fromkeys(added, True))
    _save_cache(cache)
    return len(added)


@dataclass
class ToolOutput:
    """一次 tools/call 的产出：文本 + 图像（base64 PNG，不含 data: 前缀）。

    空对象为假值，所以调用方可以直接 `if not out` 判断这次调用有没有拿到东西。
    """

    text: str = ""
    images: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.text or self.images)


def _collect(blocks: list[Any]) -> ToolOutput:
    """把 MCP 的 content blocks 拆成文本与图像。

    图像按 MAX_IMAGES 截断：一张约 14KB base64，不设上限会把响应体撑到几 MB。
    """
    texts: list[str] = []
    images: list[str] = []
    for c in blocks:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            texts.append(c.get("text") or "")
        elif c.get("type") == "image":
            data = c.get("data") or ""
            if data and len(images) < settings.MAX_IMAGES:
                images.append(data)
    return ToolOutput(text="\n".join(texts).strip(), images=images)


def _decode(resp: httpx.Response) -> dict[str, Any]:
    """把响应体解成 JSON 对象。空 body / SSE 包装 / 非 JSON 一律归一为 {}。"""
    text = (resp.text or "").strip()
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        payloads = [
            ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")
        ]
        text = payloads[-1] if payloads else ""
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _post(
    method: str, params: dict[str, Any] | None = None, notify: bool = False
) -> dict[str, Any]:
    """发一个 JSON-RPC 消息。任何失败（传输异常/空 body/非 JSON）都返回 {}。"""
    global _session_id
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = 1
    try:
        resp = httpx.post(
            settings.MCP_URL,
            json=body,
            headers=headers,
            timeout=settings.MCP_TIMEOUT,
            follow_redirects=True,
        )
    except Exception:
        return {}
    sid = resp.headers.get("mcp-session-id")
    if sid:
        _session_id = sid
    return {} if notify else _decode(resp)


def _ensure_session() -> bool:
    """惰性建会话。失败则下次调用重来（不缓存失败态，避免一次抖动让进程永久不可用）。"""
    if _session_id:
        return True
    if not _post(
        "initialize",
        {
            "protocolVersion": _PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "wolfram", "version": "0.1"},
        },
    ).get("result"):
        return False
    _post("notifications/initialized", {}, notify=True)
    return True


def call_tool(name: str, args: dict[str, Any], attempts: int = 3) -> ToolOutput | None:
    """调用一个 MCP 工具，返回文本 + 图像；失败返回 None。

    重试是必需的：实测遇到过 tools/call 返回空 body 导致解析失败的情况。
    纯图像响应（text 为 `Out[1]= `）也是成功，所以判成功看的是 ToolOutput 的真值。
    """
    for i in range(attempts):
        if _ensure_session():
            result = (
                _post("tools/call", {"name": name, "arguments": args}).get("result")
            ) or {}
            out = _collect(result.get("content") or [])
            if out:
                return out
        if i < attempts - 1:
            time.sleep(1.0 + i)
    return None


def evaluate(code: str) -> ToolOutput | None:
    """执行 WL 代码。timeConstraint 交给服务端兜底，避免长算把连接一直挂着。"""
    return call_tool(
        "WolframLanguageEvaluator",
        {"code": code, "timeConstraint": settings.EXEC_TIMEOUT},
    )


def context(query: str) -> ToolOutput | None:
    """对官方参考资料做语义检索。返回里带出的真实符号名顺手学进 system_names.json。

    保留 call_tool 的重试（默认 3 次）：实测同一条查询会「第一次回空、第二次才有内容」，
    少试一次就会把该走 codegen 的问题误判成 chat。
    """
    out = call_tool("WolframContext", {"context": query})
    if out:
        harvest_symbols(out.text)
    return out


#: W|A 查不到时写进 `<result>` 里的占位符
_NO_RESULTS = "No Results Found"


def has_answer(text: str) -> bool:
    """WolframContext 的返回里有没有**可用**内容 —— 也是 Step 1 的路由判据。

    与 Wolfram 无关的问题（写诗、自我介绍）返回空，有关的（含地理、天气）都有内容；
    唯一的坑是查不到时它仍回一段带 `No Results Found` 占位的文本，所以要剔掉占位符。
    """
    return bool((text or "").replace(_NO_RESULTS, "").strip())


def alpha(query: str) -> ToolOutput | None:
    """Wolfram|Alpha 的多视图结果，**只作 agent 的参考资料**。

    模型用它看清「同一个问题 W|A 给了哪几种表示」（符号解 / 数值近似 / 图像 / 黎曼和），
    据此决定自己该用 WL 取回哪些。它的正文不会进入对外答案，也不会进总结 —— 否则
    就等于把刚删掉的「返回 W|A 页面结果」的网页档从后门放回来。
    """
    return call_tool("WolframAlpha", {"query": query})


def available() -> bool:
    """MCP 是否可达。供 /health 与降级判断，不可达时不伪造「可用」。"""
    return _ensure_session()


def check_symbols(names: Iterable[str]) -> set[str]:
    """返回给定符号中真实存在于 System` 上下文的子集，供「函数真实性」信号使用。

    为什么不一次性拉全量 Names["System`*"] 再落盘：实测 MCP 对过长输出做省略，
    只保留头部约 6.7KB + 尾部约 5KB，中间替换成 `...`，而全量符号名约 95KB，拿不全。
    改成按需批量问，并把每个符号的结论（含否定结论）落盘缓存 —— 第二轮回落到
    同一批函数时不再打 MCP，缓存文件是 assets/system_names.json。
    """
    cache = _load_cache()
    candidates = sorted({n for n in names if _SYMBOL_RE.match(n)})
    unknown = [n for n in candidates if n not in cache]
    if unknown:
        # 一次问太多会让回显被省略，反而把真符号误判成假的，所以按批问。
        for i in range(0, len(unknown), _BATCH):
            batch = unknown[i : i + _BATCH]
            quoted = ",".join(f'"{n}"' for n in batch)
            raw = call_tool(
                "WolframLanguageEvaluator",
                {
                    "code": (
                        f"StringRiffle[Select[{{{quoted}}}, "
                        'NameQ["System`" <> #] &], "|"]'
                    ),
                    "timeConstraint": settings.EXEC_TIMEOUT,
                },
            )
            if raw is None:
                return {n for n in candidates if cache.get(n)}  # MCP 不可达：只认缓存
            body = (raw.text or "").split("=", 1)[-1].strip().strip('"')
            real = set(body.split("|"))
            for n in batch:
                cache[n] = n in real

    _save_cache(cache)
    return {n for n in candidates if cache.get(n)}
