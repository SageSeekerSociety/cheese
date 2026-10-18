"""`project_material` 手抄的 `tasks`/`accept_cards` 是别人家真表的副本 —— 钉住它。

`project_material_rows.py` 用 `table(...column(...))` 把那几列逐字抄了一遍：本包在
第 4 层，`room_task` 和 `review` 也在第 4 层，而那两个包各有一条读回本侧的边，互相
import 就是 C3 说的那条环。抄对了 SQL 一样；抄错了不报错 —— 真表改了列名，这里的
`WHERE` 还指着旧名字，单测全绿，只有连库的集成测试才炸。

和 `test_block_task_rows_match_their_rows.py` 与
`test_repository_task_rows_match_their_rows.py` 同一条做法：不连库，只比列名与类型。
真表加一列不用改这里；真表**改名或删掉**这里声明的某一列，或者改了它的类型，这里
当场红。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.sqltypes import NullType

from app.domain.project_material_rows import _cards, _tasks
from app.domain.review.models import AcceptCard
from app.domain.room_task.models import Task

#: 副本 → 它抄的那张真表。
COPIES = (
    ("_tasks", _tasks, Task),
    ("_cards", _cards, AcceptCard),
)


def test_every_declared_column_is_a_column_of_the_real_table() -> None:
    """副本上的每个列名，真表上都得有：真表改了名，这条在本地就红。"""
    for name, bare, model in COPIES:
        real = {column.name for column in model.__table__.columns}
        unknown = sorted(set(bare.c.keys()) - real)
        assert not unknown, f"{name} 抄了真表上没有的列: {unknown}"


def test_every_declared_column_keeps_the_real_type() -> None:
    """类型也对一对，但**只在真表那列自己声明的类型上**比。

    真表上有些列是 `ForeignKey("...")` 没写类型，SQLAlchemy 记的是 `NullType` ——
    它连 DDL 都生成不出来，比不了；那些列只有名字这一道，名字对不上照样红。
    声明的类型比得出来时比编译后的 SQL 类型：`DateTime` 和 `String` 换错比错列名
    更安静，读出来的东西直接就错了。
    """
    dialect = postgresql.dialect()
    for name, bare, model in COPIES:
        real = model.__table__.c
        for column_name in bare.c.keys():
            real_type = real[column_name].type
            if isinstance(real_type, NullType):
                continue  # 真表这列没声明类型（外键），只比得了名字
            copy_type = bare.c[column_name].type
            assert copy_type.compile(dialect) == real_type.compile(dialect), (
                f"{name}.{column_name} 的 SQL 类型和真表不一致"
            )


def test_the_copies_say_the_real_table_names() -> None:
    """表名也要对得上：抄成另一张表的列名，SQL 会静默读到别处。"""
    for name, bare, model in COPIES:
        assert bare.name == model.__tablename__, name


#: 这两个包里任何一层的 `models` 都不许从 `project_material` 读回来。
FORBIDDEN = ("app.domain.room_task.models", "app.domain.review.models")


def _model_imports(source: str, module: str) -> list[str]:
    """这个源文件 import 的、属于 `FORBIDDEN` 的模块。

    三种写法都能把那条边加回来，三种都要认：
    `from app.domain.room_task.models import Task`、
    `from app.domain.room_task import models`、`import app.domain.room_task.models`。
    ``module`` 是文件自己的模块名，相对 import 靠它折回绝对路径。
    """
    import ast

    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")[: -node.level]
                head = ".".join(base + (node.module.split(".") if node.module else []))
            else:
                head = node.module or ""
            targets = [head, *(f"{head}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        else:
            continue
        for target in targets:
            if target in FORBIDDEN:
                found.append(target)
                break
    return found


def test_project_material_takes_their_rows_and_not_their_models() -> None:
    """裸表副本还回来不算还：模型 import 一旦加回去，那条环就回来了。

    C3 的基线是从 merge base 量的，那棵树里 `project_material -> room_task.models`
    和 `-> review.models` 都在，所以把它们加回去检查只会回到「和基线一样」，不红。
    已经还掉的那两条边只能在这里钉住——判据和 `counterpart` 那条一样：本包只许读
    `project_material_rows` 里的副本，不许 import 那两个包的 `models`。
    """
    root = Path(__file__).resolve().parents[2]
    offenders: dict[str, list[str]] = {}
    for name in ("project_material.py", "project_material_rows.py"):
        source = (root / "app/domain" / name).read_text(encoding="utf-8")
        found = _model_imports(source, f"app.domain.{name[:-3]}")
        if found:
            offenders[name] = found
    assert not offenders, (
        f"project_material 又 import 了真模型：{offenders}。"
        "只读的那几列走 project_material_rows 的副本，不要在包内把边加回去。"
    )


def test_the_guard_would_notice_the_import_put_back() -> None:
    """证明上一条不是空转：三种写法都认得出，名字相近的包不误报。"""
    module = "app.domain.project_material"
    assert _model_imports("from app.domain.room_task.models import Task\n", module) == [
        "app.domain.room_task.models"
    ]
    assert _model_imports("from app.domain.room_task import models\n", module) == [
        "app.domain.room_task.models"
    ]
    assert _model_imports("import app.domain.review.models\n", module) == [
        "app.domain.review.models"
    ]
    assert not _model_imports(
        "from app.domain.room_task.naming import announce_stale\n", module
    ), "同包但不是 models 的模块不算"
    assert not _model_imports(
        "from app.domain.project_material_rows import _tasks\n", module
    )


def test_the_spelled_out_status_values_are_the_models_own() -> None:
    """两个状态是手抄的字符串 —— 枚举改了名，裸表读法会静默答错。

    `status` 存的是枚举成员的名字（`Enum(native_enum=False)`），而副本认不出那个
    枚举，只能把值写死。这正是**最**安静的一类漂移：列名和类型都没变，只有那一个
    字符串的意思变了，读出来的「做完了没有」悄悄反过来。所以这里也钉一下。
    """
    from app.domain.project_material_rows import STATUS_ACCEPTED, STATUS_OPEN
    from app.domain.review.models import AcceptStatus
    from app.domain.room_task.models import TaskStatus

    assert STATUS_OPEN == TaskStatus.open.value
    assert STATUS_ACCEPTED == AcceptStatus.accepted.value
