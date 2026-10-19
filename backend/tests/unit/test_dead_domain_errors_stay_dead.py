"""死异常与死 schema 的守卫：这 22 个名字已按零引用清退，不许再回到代码里。

清退依据是引用计数，不是「看着没人用」：

- `app.core.domain_errors` 里的 19 个异常类全仓只出现在定义处；其中
  `UserAlreadyMemberError`、`NotTeamMemberYetError` 只被 `test_errors.py` 测过，
  两条用例随类一起删了。
- 同一批还清退了三个请求/响应模型：`users/_common.py` 的
  `TwoFactorCodeRequest`，`project/schemas.py` 的 `TaskLinkCreate`、
  `TaskLinkOut`，全仓无人引用。

守卫扫全仓的 Python 源码：定义、import、属性访问、参数绑定和字符串常量里出现都
算回流，换一个模块重新定义一份同样红。前端按错误码字符串分支的地方是产品契约
（`TeamLockedError` 就是），不在这个守卫的范围里。

自测用临时目录里的一份源码证明扫描能看见各种写法；它在清退前后都是绿的，变的只有
仓库扫描那一条。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SELF = Path(__file__).resolve()

#: `app.core.domain_errors` 里按零引用清退的 19 个异常类。
RETIRED_ERRORS = frozenset(
    {
        "RealNameInfoRequiredError",
        "NotTaskParticipantYetError",
        "AlreadyBeTaskParticipantError",
        "TaskParticipantNotApprovedError",
        "YourRankIsNotHighEnoughError",
        "TaskSubmissionNotEditableError",
        "TaskSubmissionNotMatchSchemaError",
        "TaskRegistrationNotStartedError",
        "EmailOrPhoneRequiredError",
        "YourTeamMemberRankIsNotHighEnoughError",
        "TeamRoleConflictError",
        "UserAlreadyMemberError",
        "NotTeamMemberYetError",
        "PendingApplicationExistsError",
        "NotSpaceAdminYetError",
        "AlreadyBeSpaceAdminError",
        "AlreadyBeSpaceOwnerError",
        "RankNotEnabledForSpaceError",
        "ConversationNotFoundError",
    }
)

#: 同一批清退的三个零引用请求/响应模型。
RETIRED_SCHEMAS = frozenset(
    {
        "TwoFactorCodeRequest",
        "TaskLinkCreate",
        "TaskLinkOut",
    }
)

RETIRED = RETIRED_ERRORS | RETIRED_SCHEMAS

#: 依赖目录和缓存里不会有本仓库的源码，扫它们只会拖慢一条单测。
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)


def _python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


class _RetiredNameScan(ast.NodeVisitor):
    """一个模块里出现过的清退名字。定义与引用同等看待。

    字符串常量也扫：`getattr(errors, "UserAlreadyMemberError")` 绕开了 import 和
    名字节点，但绕不开 AST 里的字面量。
    """

    def __init__(self, names: frozenset[str]) -> None:
        self.names = names
        self.hits: list[str] = []

    def _record(self, node: ast.AST, name: str, kind: str) -> None:
        if name in self.names:
            self.hits.append(f"{getattr(node, 'lineno', '?')}: {kind} {name}")

    def _definition(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self._record(node, node.name, "definition")
        self.generic_visit(node)

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Name(self, node: ast.Name) -> None:
        self._record(node, node.id, "reference")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record(node, node.attr, "reference")
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._record(node, node.arg, "binding")
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        self._record(node, node.name.split(".")[-1], "import")
        if node.asname:
            self._record(node, node.asname, "import")

    def _binding(self, node: ast.Global | ast.Nonlocal) -> None:
        for name in node.names:
            self._record(node, name, "binding")

    visit_Global = _binding
    visit_Nonlocal = _binding

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for name in self.names:
                if name in node.value:
                    self._record(node, name, "string")


def _scan(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _python_files(root):
        if path == SELF:
            continue
        source = path.read_text(encoding="utf-8")
        # 字符串里都没有，AST 里就不可能有这个名字；跳过的只是解析，判定口径不变。
        if not any(name in source for name in RETIRED):
            continue
        scan = _RetiredNameScan(RETIRED)
        scan.visit(ast.parse(source, filename=str(path)))
        hits.extend(f"{path.relative_to(root)}:{hit}" for hit in scan.hits)
    return hits


def test_no_retired_name_is_defined_or_referenced() -> None:
    hits = _scan(REPO_ROOT)
    assert not hits, (
        "已按零引用清退的名字又出现在这些位置：" + "；".join(hits) + "。"
        "要重新引入，先说明谁调用、为什么现有的同族名字做不了这件事。"
    )


def test_the_neighbours_that_are_still_used_stay() -> None:
    """清退只删零引用的那批；同文件里还在用的四个类被顺手删掉就红。"""
    from app.core.domain_errors import (
        TaskParticipantsReachedLimitError,
        TeamLockedError,
        TeamSizeNotEnoughError,
        TeamSizeTooLargeError,
    )
    from app.core.errors import BaseError

    kept = (
        TaskParticipantsReachedLimitError,
        TeamLockedError,
        TeamSizeNotEnoughError,
        TeamSizeTooLargeError,
    )
    assert all(issubclass(error, BaseError) for error in kept)


def test_the_scan_sees_every_way_a_retired_name_could_come_back(tmp_path: Path) -> None:
    """守卫自己的自测：定义、import、属性、参数和字符串都要被看见。"""
    (tmp_path / "revived.py").write_text(
        "class UserAlreadyMemberError(Exception):\n"
        "    pass\n"
        "\n"
        "from app.core.domain_errors import ConversationNotFoundError\n"
        "from app.core import domain_errors as _errors\n"
        "\n"
        "value = _errors.TaskLinkOut\n"
        "message = 'TwoFactorCodeRequest'\n"
        "\n"
        "def render(TeamRoleConflictError):\n"
        "    return TeamRoleConflictError\n"
        "\n"
        "def rebind():\n"
        "    global PendingApplicationExistsError\n"
        "    PendingApplicationExistsError = None\n",
        encoding="utf-8",
    )

    hits = _scan(tmp_path)
    assert {hit.split()[-1] for hit in hits} == {
        "UserAlreadyMemberError",
        "ConversationNotFoundError",
        "TaskLinkOut",
        "TwoFactorCodeRequest",
        "TeamRoleConflictError",
        "PendingApplicationExistsError",
    }


def test_the_scan_ignores_a_name_that_is_not_retired(tmp_path: Path) -> None:
    """还在用的同族名字不是回流，扫描不能把它们一起报出来。"""
    (tmp_path / "alive.py").write_text(
        "from app.core.domain_errors import TeamLockedError\n"
        "\n"
        "message = 'TeamLockedError'\n",
        encoding="utf-8",
    )

    assert not _scan(tmp_path)
