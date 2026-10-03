"""第二层：Wolfram 执行器。

唯一通道是 Wolfram 官方托管的 MCP（backend/core/mcp.py）。这里原先有三档降级
（WolframAlpha 网页 → 本地 wolframscript → WolframAlpha Cloud API），已全部删除：

    网页档  靠 JS 渲染，静态 HTML 里拿不到 pod 文本，返回的是「页面结果」；
    本地档  本机没有 Wolfram Engine / wolframscript；
    云端档  要额外申请 AppID。

而留着降级链最大的代价麻烦是「执行成功」这个信号失去意义 —— 网页档返回一段页面
文本也记 ok=True，于是奖励里「执行成功」形同虚设（实测 DSolve 走网页档 verify=0%）。

所有表达式在执行前必须过 backend/core/safety.py 的符号闸门；命中即拒绝，不发任何请求。
"""

import asyncio
import time
from dataclasses import dataclass, field

from backend.core import mcp
from backend.core.config import settings
from backend.core.models import SafetyViolation
from backend.core.safety import scan


@dataclass
class ExecResult:
    ok: bool
    output: str = ""
    strategy: str = "none"  # mcp / blocked / none
    wl_code: str = ""
    error: str = ""
    latency_ms: int = 0
    violations: list[SafetyViolation] = field(default_factory=list)
    #: 求值带回的图像（base64 PNG）。Plot 这类表达式文本只有 `Out[1]= `，
    #: 真正的图在这里，不带上就等于把可视化丢了。
    images: list[str] = field(default_factory=list)


async def execute(expr: str, retries: int = 0) -> ExecResult:
    """执行一条 WL 表达式，唯一通道是 Wolfram 官方 MCP。

    retries 是「修正查询后重试」的次数（Step 5 的错误处理），内部不再自行重试，
    避免与修正重试叠乘放大调用量。
    """
    expr = (expr or "").strip()
    t0 = time.perf_counter()

    def stamp(res: ExecResult) -> ExecResult:
        res.latency_ms = int((time.perf_counter() - t0) * 1000)
        return res

    if not expr:
        return stamp(
            ExecResult(ok=False, wl_code=expr, error="表达式为空", strategy="none")
        )

    violations = scan(expr)
    if violations and settings.SAFETY_ENFORCE:
        detail = "；".join(f"{v.rule}({v.detail})" for v in violations[:3])
        return stamp(
            ExecResult(
                ok=False,
                strategy="blocked",
                wl_code=expr,
                violations=violations,
                error=f"安全闸门拒绝：{detail}",
            )
        )

    res = await asyncio.to_thread(mcp.evaluate, expr)
    if not res:
        return stamp(
            ExecResult(
                ok=False,
                strategy="mcp",
                wl_code=expr,
                error="Wolfram MCP 不可达或无返回",
            )
        )
    output, images = res.text, res.images
    # 求值失败时 MCP 也会回文本（消息 + `Out[1]= $Failed`），不能一律当成功。
    # 只认最后一行是不是 $Failed：Solve 之类的 ::ifun 提示「带警告但确实给了答案」，
    # 按消息标签判失败会把这类正常结果误伤。
    if output.rstrip().endswith("$Failed"):
        return stamp(
            ExecResult(
                ok=False,
                strategy="mcp",
                wl_code=expr,
                images=images,
                output=output[: settings.RESULT_CHARS],
                error="Wolfram 求值失败（$Failed）",
            )
        )
    return stamp(
        ExecResult(
            ok=True,
            strategy="mcp",
            wl_code=expr,
            images=images,
            output=output[: settings.RESULT_CHARS],
        )
    )
