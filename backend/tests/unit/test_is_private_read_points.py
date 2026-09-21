"""``is_private`` 的读点按文件登记，是一道棘轮（ARCH §9.1「私聊」行判据②）。

私聊是项目内名册两席的房间（结论 19）。凡是「私聊要不一样」的地方，答案都该从那
两席、或者从「这一轮不租地点」推出来；各自再问一遍这个布尔，就是同一件事有 N 份
声明，而 N 份声明各自漂移是必然的，不是可能的。

所以这里把每个文件问它几次记成一个数。加一处就红，减一处也红——减了要把基线调下
来，不然让出来的位置会被下一个人悄悄填回去。

数的是 AST 里叫 ``is_private`` 的节点（属性、名字、实参、形参），不是文本出现次
数：注释和 docstring 里说到它不算一次声明，说清楚反而是该做的事。
"""

import ast
import pathlib

#: 每个文件问 ``is_private`` 几次。
#:
#: ``agent/chat.py`` 的 1 是定死的：``_is_dm``，整个文件唯一读那个布尔的地方。
#: ARCH §9.1 判据②的两类读点都从它推出来——``_private_owner``（名册恰好两席，这
#: 间房的人是哪一位）和 ``_assemble_turn`` 的 ``needs_place``（这一轮不租地点，只
#: 有会话自己那块 64 MiB 草稿区）——所以那两类各自在的地方不再碰这个布尔。
#: 其余文件是登记，不是认可：它们多半是 ``WHERE is_private IS FALSE`` 这类「私聊
#: 不进这张列表」的过滤，和轮次组装不是一回事，各自有各自的去向。
BASELINE = {
    "app/api/auth.py": 2,
    "app/api/routes/project_environment.py": 2,
    "app/api/routes/projects.py": 1,
    "app/domain/agent/chat.py": 1,
    "app/domain/agent_instance/services.py": 1,
    "app/domain/authz/policy.py": 2,
    "app/domain/dashboard/services.py": 1,
    "app/domain/project/environment_recovery.py": 1,
    "app/domain/topic/models.py": 1,
    "app/domain/topic/repositories.py": 7,
    "app/domain/topic/services.py": 1,
}

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _names_it(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "is_private":
            count += 1
        elif isinstance(node, ast.Name) and node.id == "is_private":
            count += 1
        elif isinstance(node, ast.keyword) and node.arg == "is_private":
            count += 1
        elif isinstance(node, ast.arg) and node.arg == "is_private":
            count += 1
    return count


def _counts() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(_APP.rglob("*.py")):
        source = path.read_text()
        if "is_private" not in source:
            continue
        count = _names_it(ast.parse(source))
        if count:
            found[str(path.relative_to(_APP.parent))] = count
    return found


def test_is_private_is_asked_no_more_often_than_the_baseline():
    found = _counts()
    added = {f: n for f, n in found.items() if n > BASELINE.get(f, 0)}
    assert not added, (
        f"这些文件多问了一次 is_private：{added}。"
        "私聊要不一样的地方，答案从「名册两席」或「不租地点」推出来"
        "（结论 19，ARCH §9.1 判据②），不要在这里再问一遍这个布尔。"
    )
    dropped = {
        f: (BASELINE[f], found.get(f, 0))
        for f in BASELINE
        if found.get(f, 0) < BASELINE[f]
    }
    assert not dropped, (
        f"这些文件少问了 is_private：{dropped}（基线, 现在）。"
        "这是好事——把 BASELINE 里的数字改成现在这个，"
        "不然让出来的位置会被下一个人悄悄填回去。"
    )


def test_a_turn_asks_it_once_and_derives_the_rest():
    """整个文件只读一次那个布尔，两类答案都从它推出来。"""
    source = (_APP / "domain" / "agent" / "chat.py").read_text()
    assert _names_it(ast.parse(source)) == 1
    # 逐字点名，这样换掉那一处而总数不变的改法也会被看见。
    assert "def _is_dm(topic: Topic) -> bool:" in source, "唯一的读点"
    assert "private_seats" in source, "名册两席那一类走 TopicMemberService"
    assert "needs_place = not _is_dm(topic)" in source, "不租地点那一类"
