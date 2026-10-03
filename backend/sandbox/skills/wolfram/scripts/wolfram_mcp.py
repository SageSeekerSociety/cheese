#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx==0.27.2",
#     "pydantic==2.10.4",
# ]
# ///
"""在 Wolfram 官方 MCP 上执行 Wolfram Language：查文档、跑表达式、判真假、查精选数据。

两种用法：`call <工具> '<json>'` 跑一次工具（skill 走这条），或 `serve` 起 stdio MCP。
本机不需要任何密钥 —— Wolfram MCP 无需鉴权，表达式由调用方（agent）自己写。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import traceback
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# 源码溯源号，运行时不访问网络
UPSTREAM_COMMIT = "4bc242c2f56d2afcb6dc9f21a3431e4e3dfb80db"
SERVER_NAME = "wolfram"  # 芝士里这个能力叫 wolfram
# 版本带提交前 7 位，便于对账
SERVER_VERSION = "0.2.0+" + UPSTREAM_COMMIT[:7]
# 能回的协议版本，按客户端问的挑
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def log(*parts: Any) -> None:
    # stdout 只走协议，日志必须去 stderr
    print("[wolfram-mcp]", *parts, file=sys.stderr, flush=True)


def _vendor_dir() -> Path:
    # 源码就放在本 skill 里，随 skill 一起投递到机器上
    root = Path(__file__).resolve().parent / "_vendor"
    if not (root / "backend" / "core" / "mcp.py").is_file():
        # 缺了就说清是装包问题
        raise SystemExit(f"{root} 里没有随包的 backend 源码，这份 skill 没装全")
    return root


SOURCE = _vendor_dir()  # 导入期只需确认随包源码在
# 一次性调用落的图片文件、符号缓存都在这
STATE = Path.home() / ".local" / "share" / "wolfram"
sys.path.insert(0, str(SOURCE))  # 让下面的 backend.* 从随包源码 import

from backend.core import mcp as wmcp  # noqa: E402 - Wolfram 官方 MCP 客户端
from backend.core.config import settings  # noqa: E402 - 上游配置单例
from backend.core.executor import execute as wl_execute  # noqa: E402 - 执行通道
from backend.core.safety import scan  # noqa: E402 - 安全闸门

settings.ASSET_DIR = str(STATE / "assets")  # 符号缓存落在状态目录，不落进项目

Content = list[dict[str, Any]]  # MCP 的 content 数组


def text(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        # 非字符串一律转 JSON，中文不转义
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return {"type": "text", "text": value}  # MCP 文本块


def images(b64s: list[str], label: str = "图") -> Content:
    out: Content = []
    for i, b64 in enumerate(b64s, 1):
        # 先给标签（序号从 1 开始），模型才知道图该放哪
        out.append(text(f"[[{i}]] 第 {i} 张{label}："))
        # 再给图本身
        out.append({"type": "image", "data": b64, "mimeType": "image/png"})
    return out


def _dump(model: Any) -> Any:
    # pydantic 模型转 JSON，其他原样
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


# ===================== 工具实现 =====================


async def t_probe(a: dict[str, Any]) -> Content:
    # 探针同步阻塞，丢线程池免堵事件循环
    out = await asyncio.to_thread(wmcp.context, (a["query"] or "").strip())
    body = out.text if out else ""
    # 判据来自 Wolfram 本身，不看关键词
    mode = "codegen" if out and wmcp.has_answer(body) else "chat"
    rule = "WolframContext 有可用返回 → codegen；否则 chat"
    return [
        text({"mode": mode, "rule": rule}),  # 把判据一并说明
        text("【WolframContext 原文】\n" + (body or "（空）")),  # 原文给模型自己判断
    ]


async def t_context(a: dict[str, Any]) -> Content:
    out = await asyncio.to_thread(wmcp.context, a["query"])  # 直连官方 MCP
    body = (out.text if out else "")[: settings.DOC_CHARS]  # 按上限截断
    if not body:
        # 空返回要说明，不能装作查到了
        return [text("文档检索没有返回内容（MCP 不可达或查不到该词）")]
    return [text(f"[Wolfram 官方文档]\n{body}")]


async def t_alpha(a: dict[str, Any]) -> Content:
    out = await asyncio.to_thread(wmcp.alpha, a["query"])  # 直连
    body = (out.text if out else "")[: settings.RESULT_CHARS]  # 按参考上限截断
    if not body:
        # 空返回明确说
        return [text("Wolfram|Alpha 没有返回参考内容（MCP 不可达或查不到）")]
    return [
        text(
            "[Wolfram|Alpha 参考返回]（仅供判断该取哪几种表示，不要照抄；"
            f"最终答案必须由 WL 表达式算出来）\n{body}"
        )
    ]


async def t_execute(a: dict[str, Any]) -> Content:
    res = await wl_execute(a["code"])  # 直接过执行通道（含安全闸门）
    # 先把结论摆前面，违规明细一并给出
    head = {
        "ok": res.ok,
        "strategy": res.strategy,
        "latency_ms": res.latency_ms,
        "error": res.error,
        "violations": [_dump(v) for v in res.violations],
    }
    # 结论 + 文本 + 图
    return [text(head), text(res.output or "（无文本输出）")] + images(res.images)


async def t_check_symbols(a: dict[str, Any]) -> Content:
    names = [str(n) for n in a["names"]]  # 统一成字符串，防止调用方给数字
    # 批量判定，内部有缓存
    real = await asyncio.to_thread(wmcp.check_symbols, names)
    # 真假分开列，编造的才看得见
    return [text({"real": sorted(real), "fake": sorted(set(names) - real)})]


async def t_scan(a: dict[str, Any]) -> Content:
    hits = scan(a["code"])  # 只扫不执行，比执行一次快得多
    # 命中即不安全
    return [text({"safe": not hits, "violations": [_dump(h) for h in hits]})]


async def t_health(a: dict[str, Any]) -> Content:
    # Wolfram MCP 到底通不通
    available = await asyncio.to_thread(wmcp.available)
    return [
        text(
            {
                "status": "ok",  # 能回这个就说明进程活着
                "wolfram": {
                    "channel": "mcp",  # 唯一执行通道
                    "url": settings.MCP_URL,
                    "available": available,
                },
                "safety_enforced": settings.SAFETY_ENFORCE,  # 闸门是否开着
                "vendor_commit": UPSTREAM_COMMIT,
                "state_dir": str(STATE),
            }
        )
    ]


# ===================== 工具表 =====================

S = dict[str, Any]
STR = {"type": "string"}  # 复用同一个字符串 schema


def _obj(props: S, required: tuple[str, ...] = ()) -> S:
    return {
        "type": "object",
        "properties": props,
        # 统一 schema：属性 + 必填
        "required": list(required),
        # 收紧：多给的参数一律拒绝，防止静默忽略
        "additionalProperties": False,
    }


TOOLS: list[tuple[str, str, S, Callable[[dict[str, Any]], Awaitable[Content]]]] = [
    # (名字, 说明, 入参, 实现)
    (
        "wolfram_probe",
        "Step 1 路由探针：用 WolframContext 查一次用户原话。"
        "有可用返回就是 codegen（走 Wolfram 计算），否则是 chat"
        "（直接用自然语言回答，不写 WL）。判据来自 Wolfram 本身，"
        "不看关键词。",
        _obj({"query": STR}, ("query",)),
        t_probe,
    ),
    (
        "wolfram_context",
        "查 Wolfram 官方参考资料（函数名、语法、参数）。"
        "不确定函数怎么写时才用，查到就照文档写，不凭印象编函数名。",
        _obj({"query": STR}, ("query",)),
        t_context,
    ),
    (
        "wolfram_alpha",
        "看 Wolfram|Alpha 对同一问题给出哪几种表示，只作参考：它的正文"
        "不能进最终答案，答案必须由你自己的 WL 表达式算出来。"
        "拿不准该给用户哪些表示时才用。",
        _obj({"query": STR}, ("query",)),
        t_alpha,
    ),
    (
        "wolfram_execute",
        "在 Wolfram 官方 MCP 上执行 WL 表达式（唯一执行通道），"
        "先过安全闸门（文件、进程、网络类符号一律拒绝）。"
        "最后一行是 $Failed 判为失败。图像以图片块返回。所有计算都走这里。",
        _obj({"code": STR}, ("code",)),
        t_execute,
    ),
    (
        "wolfram_check_symbols",
        "判定一组函数名是否为真实的 System` 符号（带本地缓存），用来发现编造的函数名。",
        _obj({"names": {"type": "array", "items": STR}}, ("names",)),
        t_check_symbols,
    ),
    (
        "wolfram_safety_scan",
        "只做安全扫描不执行：列出表达式命中的禁止符号与语法糖"
        "（!cmd、<<file、>>file）。",
        _obj({"code": STR}, ("code",)),
        t_scan,
    ),
    (
        "wolfram_health",
        "Wolfram MCP 通不通、安全闸门是否开启，以及随包源码与状态目录的位置。",
        _obj({}),
        t_health,
    ),
]
# 名字到实现的索引，serve 与 call 共用
HANDLERS = {name: fn for name, _, _, fn in TOOLS}

INSTRUCTIONS = """把自然语言科研需求变成 Wolfram Language，在 Wolfram 官方
MCP 上执行，再用自然语言讲清结果。

处理用户的计算类问题时：
1. 先 wolfram_probe 用户原话。mode=chat 就直接用自然语言回答，不写 WL。
2. mode=codegen：自己把用户的话改写成一条 WL 表达式——补全区间与单位、
   口语转精确英文、消解指代，并把有用的东西一次取回（主结果 + N[...] 数值近似 +
   真正有用的图，用 {...}）。
3. 不确定函数名或语法用 wolfram_context 查官方文档；拿不准该给哪几种表示用
   wolfram_alpha，它只作参考，不进答案。表达式先 wolfram_execute 真跑一遍，
   按返回的 $Failed 或报错改，不凭印象断言结果。
4. 按执行结果与图像写一段连贯中文总结，图用 [[n]] 标在该出现的位置；
   不贴代码，不编数值。
5. 文件读写、系统命令、网络请求类符号会被安全闸门拒绝，不要尝试绕过。
Wolfram MCP 不可达时（wolfram_health 的 available=false）如实说明，
不要用网页结果或凭记忆冒充计算结果。
本 skill 对外的工具就是上面这张表里的 wolfram_*。"""


# ===================== JSON-RPC over stdio =====================


class Server:
    def __init__(self) -> None:
        # 并发处理时 stdout 必须串行写，否则行会被撕开
        self.out_lock = asyncio.Lock()
        self.tasks: set = set()  # 活着的任务，退出前要等它们收尾

    async def send(self, msg: dict[str, Any]) -> None:
        # 一行一条消息
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        async with self.out_lock:
            # 直接写字节，不经过可能加换行的文本层
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()  # 必须立刻刷，对端在等

    async def reply(
        self, mid: Any, result: Any = None, error: dict[str, Any] | None = None
    ) -> None:
        # 响应必需的三个字段
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid}
        if error is not None:
            msg["error"] = error  # 错误响应
        else:
            msg["result"] = result  # 成功响应
        await self.send(msg)

    async def handle(self, msg: dict[str, Any]) -> None:
        # 一次解出 method / id / params 三样
        method, mid, params = (
            msg.get("method"),
            msg.get("id"),
            msg.get("params") or {},
        )
        if method is None or mid is None:
            return  # 通知与客户端回给我们的响应都不用答
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")  # 客户端想用的版本
                await self.reply(
                    mid,
                    {
                        # 支持就用客户端要的
                        "protocolVersion": (
                            asked if asked in PROTOCOLS else PROTOCOLS[0]
                        ),
                        # 不做工具变更通知
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                        "instructions": INSTRUCTIONS,  # 用法说明随握手一起给
                    },
                )
            elif method == "ping":
                await self.reply(mid, {})  # 心跳
            elif method == "tools/list":
                await self.reply(
                    mid,
                    {
                        # 名字/说明/入参
                        "tools": [
                            {"name": n, "description": d, "inputSchema": s}
                            for n, d, s, _ in TOOLS
                        ]
                    },
                )
            elif method == "tools/call":
                # 分派到工具
                await self.reply(
                    mid,
                    await self.call(
                        params.get("name", ""), params.get("arguments") or {}
                    ),
                )
            else:
                # 标准的方法不存在
                await self.reply(
                    mid, error={"code": -32601, "message": f"未知方法 {method}"}
                )
        except Exception as e:  # noqa: BLE001
            log("处理失败", method, traceback.format_exc())  # 栈写日志
            # 对端只拿摘要
            await self.reply(
                mid, error={"code": -32603, "message": f"{type(e).__name__}: {e}"}
            )

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = HANDLERS.get(name)
        if fn is None:
            # 未知工具报给模型
            return {"content": [text(f"未知工具 {name}")], "isError": True}
        try:
            return {"content": await fn(args), "isError": False}  # 正常结果
        except KeyError as e:
            # 少参数是最常见的调用错误
            return {"content": [text(f"缺少参数 {e}")], "isError": True}
        except Exception as e:  # noqa: BLE001
            log("工具失败", name, traceback.format_exc())  # 栈写日志
            # 工具级失败不拖垮进程
            return {
                "content": [text(f"{type(e).__name__}: {e}")],
                "isError": True,
            }

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()  # 要把 stdin 接到 asyncio
        # 上限放宽：一次调用可能塞很长的表达式
        reader = asyncio.StreamReader(limit=32 * 1024 * 1024)
        # 接上 stdin
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        while True:
            line = await reader.readline()
            if not line:
                # EOF：调用方关了 stdin 就收工，这让"喂一次就跑一次"也成立
                break
            line = line.strip()
            if not line:
                continue  # 空行跳过
            try:
                msg = json.loads(line)  # 一行一条消息
            except Exception:  # noqa: BLE001
                # 解析错误要回，id 只能是 null
                await self.reply(
                    None, error={"code": -32700, "message": "不是合法 JSON"}
                )
                continue
            # 允许批量
            for item in msg if isinstance(msg, list) else [msg]:
                # 并发处理：慢调用不堵后续请求
                task = asyncio.create_task(self.handle(item))
                self.tasks.add(task)  # 记下，退出前要等
                task.add_done_callback(self.tasks.discard)  # 完成即摘掉
        if self.tasks:
            # 给在飞的任务 5s 收尾，不无限等
            await asyncio.wait(self.tasks, timeout=5)


async def _once(
    fn: Callable[[dict[str, Any]], Awaitable[Content]], args: dict[str, Any]
) -> int:
    parts = await fn(args)  # 跑一次
    seen = 0  # 图片序号，和 [[n]] 占位符对齐
    for c in parts:
        if c["type"] == "text":
            print(c["text"])  # 文本原样打给调用方
        elif c["type"] == "image":
            seen += 1
            # CLI 没有图片块通道，落成文件让 agent 自己 Read
            dest = STATE / "images"  # 落在状态目录下，不污染项目仓库
            dest.mkdir(parents=True, exist_ok=True)  # 首次调用时建
            png = dest / f"{uuid.uuid4().hex[:8]}.png"  # 随机名，避免并发覆盖
            png.write_bytes(base64.b64decode(c["data"]))  # MCP 里图是 base64
            # 把路径给出去，agent 用 Read 打开就能看见
            print(f"[[{seen}]] 图片文件：{png}")
    return 0


def cli(argv: list[str]) -> int:
    # 缺省即 serve
    ap = argparse.ArgumentParser(
        prog="wolfram_mcp.py", description="缺省（或 serve）是 stdio MCP 服务"
    )
    sub = ap.add_subparsers(dest="cmd")  # 子命令
    sub.add_parser("serve", help="stdio MCP 服务")  # 长驻，工具全可用
    # skill 走这条
    ca = sub.add_parser(
        "call", help="一次性调用某个工具，结果打到 stdout（进程不常驻）"
    )
    # 工具名
    ca.add_argument(
        "tool",
        nargs="?",
        default=None,
        help="工具名，同 tools/list 里的名字；不填就列出全部",
    )
    # 入参
    ca.add_argument("args", nargs="?", default="{}", help="工具的入参 JSON，缺省 {}")
    sub.add_parser("health", help="Wolfram MCP 通不通")  # 只读
    ns = ap.parse_args(argv)

    if ns.cmd in (None, "serve"):
        asyncio.run(Server().serve())  # 缺省即长驻服务
        return 0
    if ns.cmd == "call":
        if ns.tool is None:  # 不带工具名就是问"有哪些工具"
            log("可用工具：" + "、".join(HANDLERS))  # 列出来，省得靠猜
            return 0
        if ns.tool not in HANDLERS:
            # 打错工具名要给出可用清单
            log(f"没有这个工具：{ns.tool}。可用：" + "、".join(HANDLERS))
            return 1
        # 入参不合法会在这一步抛，退出码非 0
        return asyncio.run(_once(HANDLERS[ns.tool], json.loads(ns.args or "{}")))
    return asyncio.run(_once(t_health, {}))  # health


def main() -> None:
    sys.exit(cli(sys.argv[1:]))  # 把退出码透传给调用方


if __name__ == "__main__":
    main()  # skill 里用 uv run --script 直接执行本文件
