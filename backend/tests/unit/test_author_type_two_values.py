"""守卫：事件行的作者只分「参与者」和「平台」两档。

和 ``test_no_adhoc_auth_helpers.py`` 一样，这是一条**静态**测试 —— 被测的东西本身
就是源码树的一个性质，属于 CLAUDE.md 那条「测行为、不读源码」的例外。

它守的是这次改动最容易悄悄退回去的一半。合并档位的难处不在改枚举，在于全仓 30 多
处读点各自用 ``author_type == ai`` 回答了一个它答不了的问题（「这句是不是芝士说
的」）。只要枚举里还留着 ``human``/``ai``，下一个人写下 ``AuthorType.ai`` 时什么
也不会响，而它会正确地跑一年 —— 存量行还带着那个值。

所以判据是：那两个旧值只有 ``block/authorship.py`` 读得到。它是唯一知道「旧值也
是参与者」这件事的地方，P8b 把存量行改写完就连它一起删。

第二条判据补的是第一条抓不到的那一半。``AuthorType.ai`` 是属性访问，``ast`` 一抓
一个准；而 ``author_type.value in {"human": 0, "ai": 0}`` 里旧值是一个**字符串字面
量**，名字从来没出现过 —— 活跃度统计就是这样漏过去的，它不会报错，只会安静地把两
个数字都算成 0。所以第二条不去追字面量（``"human"``/``"ai"`` 在别的地方有别的意
思），而是追**把 ``author_type`` 当字符串读**这件事本身：``author_type.value`` 和
``author_type == "..."``。档位是一个两档枚举，取出它的字符串再拿去对照或做键，问的
一定是它答不了的那个问题。
"""

import ast
import pathlib

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

_LEGACY = {"human", "ai"}

# 只有这一个模块读得到旧值：它就是把旧值翻译成「参与者」的那一层。
ALLOWED = {"domain/block/authorship.py"}


def _legacy_author_type_lines(tree: ast.AST) -> list[int]:
    """``AuthorType.human`` / ``AuthorType.ai`` 出现在哪几行。"""
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in _LEGACY
            and isinstance(node.value, ast.Name)
            and node.value.id == "AuthorType"
        }
    )


def _author_type_as_a_string_lines(tree: ast.AST) -> list[int]:
    """把 ``author_type`` 当字符串读的那几行。"""

    def names_the_column(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "author_type"
        return isinstance(node, ast.Attribute) and node.attr == "author_type"

    def against_a_string(node: ast.Compare) -> bool:
        sides = ((node.left, node.comparators[0]), (node.comparators[0], node.left))
        return any(
            names_the_column(column)
            and isinstance(other, ast.Constant)
            and isinstance(other.value, str)
            for column, other in sides
        )

    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "value"
            and names_the_column(node.value)
        ):
            lines.add(node.lineno)
        elif isinstance(node, ast.Compare) and against_a_string(node):
            lines.add(node.lineno)
    return sorted(lines)


def test_only_authorship_reads_the_two_legacy_author_types() -> None:
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for lineno in _legacy_author_type_lines(ast.parse(path.read_text())):
            offenders.append(f"backend/app/{rel}:{lineno}")

    assert not offenders, (
        "这些地方还在用事件行的档位回答「是人还是芝士」：\n  "
        + "\n  ".join(offenders)
        + "\n\n写入端一律 AuthorType.participant；要问「这句是不是芝士说的」用署名"
        "（app.domain.identity.handles 的 looks_like_agent_handle / "
        "agent_handle_column），要问「是参与者还是平台」用 "
        "app.domain.block.authorship。"
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """改了名或删了文件的豁免要跟着删，否则守卫上就留了个洞。"""
    missing = [rel for rel in sorted(ALLOWED) if not (APP_ROOT / rel).is_file()]
    assert not missing, f"ALLOWED 里的文件已经不存在了：{missing}"


def test_no_one_reads_the_author_type_column_as_a_string() -> None:
    """取出档位的字符串，为的只会是拿它去对照旧值 —— 而它已经答不了那个问题。"""
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).as_posix()
        for lineno in _author_type_as_a_string_lines(ast.parse(path.read_text())):
            offenders.append(f"backend/app/{rel}:{lineno}")

    assert not offenders, (
        "这些地方把事件行的档位取成字符串再用：\n  "
        + "\n  ".join(offenders)
        + "\n\n档位只有「参与者」和「平台」两档，取它的字符串去当字典键或者比对，"
        "问的一定是署名才答得了的问题（looks_like_agent_handle），"
        "而档位本身用 app.domain.block.authorship 判。"
    )
