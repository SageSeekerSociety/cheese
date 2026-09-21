"""守卫：名册只有一个读法，没人再就地拼第二份。

这是一条**静态**测试，和 ``test_no_adhoc_auth_helpers`` 同一个例外：要守的东西本身
就是源码树的一条性质。拼名册的那些写法每一处都答得出正确的 JSON——它们唯一的症状
是两份答案会在某一个读者身上分叉，而上一次分叉的那个读者是 agent，人看不见（一个
agent 列不出同项目的另一个 agent，结论 12 因此在代码上无法执行）。

所以守的是「谁在读人那一半」和「界面上还有没有就地拼接」，不是某个函数名。
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
APP = BACKEND / "app"
#: 脚本也要守：它们和 ``app/`` 跑的是同一个领域层，而一个读错名册的脚本没有界面
#: 会在上面报错——只有跑它的人会看见一个 ``AttributeError``。
SCRIPTS = BACKEND / "scripts"
FRONTEND = REPO / "frontend" / "src"

#: 人那一半（``people``）的合法读者：定义它的两个文件，加上把它和队友合成一张名册
#: 的那一个。多一个就是第二份名册——它答得出的问题和 ``roster()`` 答得出的不一样。
_PEOPLE_READERS = {
    "app/domain/project/repositories.py",
    "app/domain/project/services.py",
    "app/domain/membership/roster.py",
}

#: 把人和队友就地拼成一张名册的写法，两个次序各一条。大小写不敏感：
#: ``projectAgents`` 和 ``agents`` 是同一件事。
_STITCH = re.compile(
    r"\[\s*\.\.\.[\w$.]*(?:people|members|humans)[\w$.]*\s*,\s*\.\.\.[\w$.]*agents?[\w$.]*"
    r"|\[\s*\.\.\.[\w$.]*agents?[\w$.]*\s*,\s*\.\.\.[\w$.]*(?:people|members|humans)[\w$.]*",
    re.IGNORECASE,
)

_PEOPLE_CALL = re.compile(r"\.people\(")


def _sources(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix in suffixes and p.is_file())


def test_people_is_read_only_where_the_roster_is_composed():
    offenders = [
        str(path.relative_to(BACKEND))
        for path in _sources(APP, (".py",)) + _sources(SCRIPTS, (".py",))
        if _PEOPLE_CALL.search(path.read_text(encoding="utf-8"))
        and str(path.relative_to(BACKEND)) not in _PEOPLE_READERS
    ]
    assert not offenders, (
        "「这个项目里有谁」只有 membership/roster.py 的 roster() 一个读法；"
        f"这些文件直接读了人那一半：{offenders}"
    )


def test_nobody_stitches_a_roster_out_of_people_and_agents():
    offenders = []
    for path in (
        _sources(APP, (".py",))
        + _sources(SCRIPTS, (".py",))
        + _sources(FRONTEND, (".ts", ".vue"))
    ):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _STITCH.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert not offenders, (
        "名册不在调用点拼：人和队友是同一张名册的两个来源，合的地方只有一处。\n"
        + "\n".join(offenders)
    )
