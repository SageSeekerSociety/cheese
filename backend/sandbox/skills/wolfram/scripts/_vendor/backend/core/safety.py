"""安全沙箱闸门（对应指令 12）：Wolfram 表达式的禁止操作清单。

agent 内核用 Python AST 扫描；Wolfram 没有可直接复用的 AST（无引擎、无 parser
依赖），因此这里是「符号级」检查，等价于 WL 里的 SafeSymbolQ：

    1. 剥掉注释 (* *) 与字符串字面量 ""（那里的名字只是文本）
    2. 抽出余下文本里的标识符
    3. 命中禁止清单即拒绝，未命中才允许送入第二层
    4. `!cmd`、`<<file`、`>>file` 三种语法糖单独拦（它们分别等价于 Run / Get / Put）

它是一道「禁止操作清单」闸门 —— 真正的隔离要靠第二层进程/网络边界。
`FreeformEvaluate` 返回 HoldComplete[...] 时，必须先剥壳再扫（见 unwrap_hold）。
"""

import re

from backend.core.models import SafetyViolation

#: 禁止符号 -> 违规规则名
FORBIDDEN_SYMBOLS = {
    # 系统命令
    "Run": "symbol:Run",
    "RunProcess": "symbol:RunProcess",
    "StartProcess": "symbol:StartProcess",
    "SystemOpen": "symbol:SystemOpen",
    "ExternalEvaluate": "symbol:ExternalEvaluate",
    "ExternalOperation": "symbol:ExternalOperation",
    "Install": "symbol:Install",
    "LoadJavaClass": "symbol:LoadJavaClass",
    # 文件系统
    "DeleteFile": "symbol:DeleteFile",
    "DeleteDirectory": "symbol:DeleteDirectory",
    "RenameFile": "symbol:RenameFile",
    "RenameDirectory": "symbol:RenameDirectory",
    "CopyFile": "symbol:CopyFile",
    "CreateDirectory": "symbol:CreateDirectory",
    "SetDirectory": "symbol:SetDirectory",
    "ResetDirectory": "symbol:ResetDirectory",
    "OpenWrite": "symbol:OpenWrite",
    "OpenAppend": "symbol:OpenAppend",
    "OpenRead": "symbol:OpenRead",
    "Put": "symbol:Put",
    "PutAppend": "symbol:PutAppend",
    "Save": "symbol:Save",
    "Get": "symbol:Get",
    "Import": "symbol:Import",
    "Export": "symbol:Export",
    # 网络
    "URLSubmit": "symbol:URLSubmit",
    "URLExecute": "symbol:URLExecute",
    "URLFetch": "symbol:URLFetch",
    "URLRead": "symbol:URLRead",
    "URLSave": "symbol:URLSave",
    "SocketConnect": "symbol:SocketConnect",
    "SocketListen": "symbol:SocketListen",
    "SendMail": "symbol:SendMail",
    "ServiceExecute": "symbol:ServiceExecute",
}

#: 语法糖：正则直接命中即为违规
_SUGAR = (
    (re.compile(r"^\s*!", re.M), "sugar:!cmd", "以 ! 开头的 shell 命令（等价 Run）"),
    (re.compile(r"<<\s*\S"), "sugar:<<file", "Get 简写（读取文件）"),
    # 只拦 >>，不拦单目 > —— 后者是 Greater，`a -> b` / `a >= b` 太常见，
    # 一并拦会把正常数学表达式全判违规。
    (re.compile(r">>\s*\S"), "sugar:>>file", "PutAppend 简写（写入文件）"),
)

_IDENT = re.compile(r"\$?[A-Za-z][A-Za-z0-9]*")
_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def unwrap_hold(expr: str) -> str:
    """剥掉 HoldComplete[...] / Hold[...] 外壳，返回真正会被 ReleaseHold 执行的表达式。

    指令 12.3：FreeformEvaluate 返回的是 HoldComplete 包裹的表达式，直接 ReleaseHold
    等于跳过检查，所以检查必须作用在剥壳后的这一层。
    """
    src = (expr or "").strip()
    m = re.match(r"^(?:HoldComplete|Hold)\s*\[(.*)\]\s*$", src, re.S)
    return m.group(1).strip() if m else src


def scrub(expr: str) -> str:
    """去掉注释与字符串字面量，只留可执行部分。"""
    return _STRING.sub('""', _COMMENT.sub(" ", expr or ""))


def scan(expr: str) -> list[SafetyViolation]:
    """扫描 Wolfram 表达式里的越界操作，返回空列表 = 未命中任何红线。"""
    src = scrub(unwrap_hold(expr))
    if not src.strip():
        return []

    found: list[SafetyViolation] = []
    seen: set = set()

    def add(rule: str, detail: str, line: int = 0) -> None:
        if rule in seen:
            return
        seen.add(rule)
        found.append(SafetyViolation(rule=rule, detail=detail, line=line))

    for pattern, rule, detail in _SUGAR:
        if pattern.search(src):
            add(rule, detail)

    for sym in _IDENT.findall(src):
        if sym in FORBIDDEN_SYMBOLS:
            add(FORBIDDEN_SYMBOLS[sym], f"禁止调用 {sym}")

    return found
