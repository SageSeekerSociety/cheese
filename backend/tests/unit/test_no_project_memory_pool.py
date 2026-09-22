"""守卫：产品代码里没有一处读写项目记忆池（结论 7，不变量 I15①）。

人和 agent 共同看的只能是文档。这一档还留在枚举里，只因为库里那批旧行要landing
两次才敢删（迁移 `a1c4e8f30b26` 搬、P36b 核对后删）——而枚举里留着一个名字，下
一个人写下 `MemoryScope.project` 时什么也不会响：那次写入会安静地落进一个没有任
何读者的池子，而他以为自己让所有人都看到了。现在它会响。

扫的是 `backend/app`，产品代码那一份。`backend/alembic` 不扫：迁移是已经发生过的
历史，而那批行的 scope 正是它要点名的东西。`backend/tests` 也不扫：store 那一层是
按 (scope, scope_id) 存取的通用代码，用哪个档位当键与这条守卫无关。

这是一条静态测试——被守的东西本身就是源码树的一个性质，属于 CLAUDE.md「测行为、
不读源码」的那条例外（同 `test_author_type_two_values.py`）。
"""

import ast
import pathlib

from app.domain.memory.models import MemoryScope

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

SCANNED = "backend/app"

RETIRED = "project"


def _sources() -> list[tuple[pathlib.Path, str]]:
    root = REPO_ROOT / SCANNED
    assert root.is_dir(), f"扫描根已经不存在了：{SCANNED}"
    return [
        (p, p.relative_to(REPO_ROOT).as_posix()) for p in sorted(root.rglob("*.py"))
    ]


def _reads_the_retired_pool(tree: ast.AST) -> list[int]:
    """`MemoryScope.project` 出现的每一行。

    取属性就是要拿它当一个池的键用——读也好写也好，两边都是这条守卫要拦的。
    枚举自己那一行不会命中：``project = "project"`` 是一次赋值，不是属性访问。
    """
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == RETIRED
            and isinstance(node.value, ast.Name)
            and node.value.id == "MemoryScope"
        }
    )


def test_the_pool_is_still_in_the_enum_until_its_rows_have_landed_twice() -> None:
    """这一档要等它的行在文档里核对过才离场，所以现在还必须在。

    提前删掉，库里那批行一取就是 ``LookupError``，而它们不可再生（结论 61）。
    """
    assert RETIRED in {member.value for member in MemoryScope}


def test_no_product_code_reads_or_writes_the_project_pool() -> None:
    offenders: list[str] = []
    for path, rel in _sources():
        for lineno in _reads_the_retired_pool(ast.parse(path.read_text())):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "这些地方还在读写项目记忆池：\n  "
        + "\n  ".join(offenders)
        + "\n\n没有项目记忆池（结论 7）。所有人都该看见的事实写进项目总览的实况"
        "文档（`cheese remember --everyone`，落到根房间的 doc 块）；一个实例自己"
        "学到的写进它自己的池（`memory_pool`）。"
    )
