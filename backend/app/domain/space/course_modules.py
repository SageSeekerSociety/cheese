"""一门课开着哪几个模块 —— 声明式数据，不是代码分支。

**开关只决定界面露出哪几格**（`frontend/src/lib/courseNav.ts` 与课程首页），
**不决定能力**：关掉一个模块是把它从界面上收起来，地址仍然打得开、接口照样
答话、别的地方照旧能用。所以这里既没有「模块可不可用」，也没有「模块开不开
要改行为」—— 只有一份名字表与一次清洗，读侧永远只把它当过滤条件用，不写成
`if module_enabled:` 撒在各处。

**缺省是开着。** `{}` 与 `{"quiz": true}` 等价，所以老题目板与新建的课都不必
写全表，存的只是**偏离缺省**的那些格子。以后加一个新模块，既有的课自动拿到
它，不需要数据迁移。

(放在 `app.domain.space` 而不是别处，是因为它描述的是一块题目板 —— 一门课 ——
自己的版面；`Space.course_modules` 那一列是它唯一的落点。)
"""

from collections.abc import Mapping

#: Every module a course may declare. The order is the order the 配置页 lists
#: them in, so it reads like the course does: 教学内容 → 干什么 → 和谁一起 →
#: 管理员从中看出什么.
MODULE_KEYS: tuple[str, ...] = (
    "units",  # 教学单元：这门课的时间线
    "assignments",  # 作业与验收
    "quiz",  # 小测
    "team",  # 组队
    "stuck",  # 共性问题（管理员看成员卡在哪）
    "materials",  # 课件库
    "progress",  # 进度表（测试点通过率）
    "pool",  # 额度池
)


def normalize(modules: Mapping[str, object] | None) -> dict[str, bool]:
    """The storable form: known keys only, coerced to bool.

    Lenient on purpose — this runs on the read path too, where raising would let
    one bad byte in a 题目版's JSON take down every page that renders it. The
    write path is where a person is looking and can be told; see
    `PatchSpaceRequest`.
    """
    if not isinstance(modules, Mapping):
        return {}
    return {key: bool(modules[key]) for key in MODULE_KEYS if key in modules}


def is_on(modules: Mapping[str, object] | None, key: str) -> bool:
    """Whether one module is on. Absent means on (see the module docstring)."""
    if key not in MODULE_KEYS:
        raise ValueError(f"unknown course module: {key}")
    if not isinstance(modules, Mapping):
        return True
    return bool(modules.get(key, True))
