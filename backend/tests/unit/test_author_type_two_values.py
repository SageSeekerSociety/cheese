"""守卫：事件行的作者只分「参与者」和「平台」两档。

第一条判据是枚举自己：成员集合恰好是那两个。合并档位的难处从来不在改枚举，在于
全仓 30 多处读点各自用 ``author_type == ai`` 回答了一个它答不了的问题（「这句是
不是芝士说的」）；只要枚举里还留着第三档，下一个人写下 ``AuthorType.ai`` 时什么
也不会响。现在它会响：那个名字不存在了，一取就是 ``AttributeError``。

第二条判据补的是第一条抓不到的那一半。``AuthorType.ai`` 是属性访问，没了就炸；
而 ``author_type.value in {"human": 0, "ai": 0}`` 里旧值是一个**字符串字面量**，
名字从来没出现过 —— 活跃度统计就是这样漏过去的，它不会报错，只会安静地把两个数
字都算成 0。所以第二条不去追字面量（``"human"``/``"ai"`` 在别的地方有别的意思），
而是追**把 ``author_type`` 当字符串读**这件事本身：``author_type.value``、
``author_type == "..."`` 和 ``block["author_type"] == "..."``。档位是一个两档枚
举，取出它的字符串再拿去对照或做键，问的一定是它答不了的那个问题。

和 ``test_no_adhoc_auth_helpers.py`` 一样，第二条是一条**静态**测试 —— 被测的东
西本身就是源码树的一个性质，属于 CLAUDE.md 那条「测行为、不读源码」的例外。

**扫的不止 ``backend/app``。** 写进真实数据库的不只是服务进程：种子脚本
（``backend/scripts/seed_demo.py`` 写在 ``docs/workflows.md`` 的开发流程里）、
一次性回填脚本、evals 和 probe 都连着同一张 ``blocks`` 表，而一条写着枚举里没有
的值的行，下一次被取到就是 ``LookupError``。``backend/alembic`` 不扫：迁移是已经
发生过的历史，里面的字面量改一个字都是在改历史。
"""

import ast
import pathlib

from app.domain.block.models import AuthorType

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 连着同一张 blocks 表的、活的代码。
SCANNED = ("backend/app", "backend/scripts", "scripts", "evals")


def _sources() -> list[tuple[pathlib.Path, str]]:
    """被扫的每个文件，连同它在仓库里的路径。"""
    found: list[tuple[pathlib.Path, str]] = []
    for root in SCANNED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            found.append((path, path.relative_to(REPO_ROOT).as_posix()))
    return found


def _author_type_as_a_string_lines(tree: ast.AST) -> list[int]:
    """把 ``author_type`` 当字符串读的那几行。"""

    def names_the_column(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "author_type"
        if isinstance(node, ast.Subscript):
            # `block["author_type"]`：JSON 那一侧读的是同一列，问的是同一个问题。
            key = node.slice
            return isinstance(key, ast.Constant) and key.value == "author_type"
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


def test_the_column_answers_participant_or_platform_and_nothing_else() -> None:
    """再加一档就是再一次「按种类分叉」，而那正是这一列被改掉的理由。"""
    assert {member.value for member in AuthorType} == {"participant", "platform"}


def test_every_scanned_root_still_exists() -> None:
    """扫描根改了名，守卫就一个文件都不扫了，而且照样是绿的。"""
    missing = [root for root in SCANNED if not (REPO_ROOT / root).is_dir()]
    assert not missing, f"扫描根已经不存在了：{missing}"


def test_no_one_reads_the_author_type_column_as_a_string() -> None:
    """取出档位的字符串，为的只会是拿它去对照旧值 —— 而它已经答不了那个问题。"""
    offenders: list[str] = []
    for path, rel in _sources():
        for lineno in _author_type_as_a_string_lines(ast.parse(path.read_text())):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "这些地方把事件行的档位取成字符串再用：\n  "
        + "\n  ".join(offenders)
        + "\n\n档位只有「参与者」和「平台」两档，取它的字符串去当字典键或者比对，"
        "问的一定是署名才答得了的问题（looks_like_agent_handle），"
        "而档位本身用 app.domain.block.authorship 判。"
    )
