"""平台不因为自己的东西改被托管的仓库（结论 49，不变量 I21b）。

被托管的仓库里只能有 agent 替用户做的活。平台的配置、钩子、技能、系统提示词、
进度、记忆、实况文档、资料库、草稿、备份，一样都不进项目的 git 树，**也不以未跟
踪文件的形式落在检出目录里**——后半句才是难的那半句：一个没人 `git add` 的文件不
会出现在任何提交里，它只是永远待在别人的 `git status` 里，而删掉它的人不是我们。

这条规则的可判形式不是「扫一遍全仓，看有没有哪个写文件的调用的目标路径以检出目
录开头」。那是跨过程的路径前缀分析：路径在运行时拼出来，AST 看不见，而真正写下
去的那一步在另一台机器上。换成两条能判的：

① 往远端机器写文件的调用，`app/` 里只有一处，就是 `place.write`；
② `place.write` 自己断言目标落在 `footprint_root()` 之下、且不在检出目录里。

两条合起来说的是同一件事：写点只有一个，那一个自己守着前缀。
"""

import ast
import uuid
from pathlib import Path

import pytest

from app.domain.agent import device_provider, place

APP = Path(place.__file__).resolve().parents[2]

#: 连接器那条：`hub.put_file(...)` 把字节交给 `file.put`。
PUT_FILE = "put_file"
#: 执行器那条：control 的方法名是通用的 `control`/`call_executor`，分得出它写不写
#: 文件的只有 subtype，所以按请求体里的那个字面量抓。
STAGE_FILE = "stage_file"


def call_sites(tree: ast.AST) -> list[int]:
    """**请求**一次远端写入的那些行。

    抓的是发起的那一侧，不是接收的那一侧。`runtime.py` 里 `kind == "stage_file"`
    是执行器在机器上读自己收到的请求——它是这条规则的被守方，不是它的违反者，而一
    条把两者混为一谈的守卫会逼人把接收端也改写成别的样子来让它变绿。
    """
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == PUT_FILE:
            lines.append(node.lineno)
        for payload in [*node.args, *(word.value for word in node.keywords)]:
            if not isinstance(payload, ast.Dict):
                continue
            for key, value in zip(payload.keys, payload.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "subtype"
                    and isinstance(value, ast.Constant)
                    and value.value == STAGE_FILE
                ):
                    lines.append(node.lineno)
    return lines


def test_only_one_call_in_the_app_writes_a_file_onto_a_machine():
    """写远端文件的调用点数 == 1，而那一处在 `place.py` 里。

    数调用点，而不是读路径：路径是运行时拼的，调用点不是。一个新的写点在这里是一
    条失败的断言，在生产上是一张没人能解释的未跟踪文件——它在别人的仓库里，而看见
    它的人不知道它是谁放的，也不知道删掉会不会弄坏什么。
    """
    found: dict[str, list[int]] = {}
    for source in APP.rglob("*.py"):
        if source.name == "place.py" and source.parent.name == "agent":
            continue
        lines = call_sites(ast.parse(source.read_text()))
        if lines:
            found[str(source.relative_to(APP))] = lines
    assert found == {}, (
        "这些地方直接往机器上写文件，绕过了 place.write 的前缀断言："
        f"{found}"
    )


def test_the_one_call_is_in_place_write():
    """反过来的一半：`place.py` 里确实有那一处，而且在 `write` 里面。

    少了这一半，把 `place.write` 整个删掉也能让上面那条绿——一条永远为真的守卫，
    和没有守卫是同一件事。
    """
    module = ast.parse((APP / "domain/agent/place.py").read_text())
    write = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "write"
    )
    assert call_sites(write), "place.write 不再自己写文件了"


class Machine:
    """答应下来并记下它被要求写到哪里的一台机器。"""

    def __init__(self) -> None:
        self.wrote: list[tuple[str, bytes]] = []

    async def put_file(self, device_id, sid, path, data, *, timeout=30):
        del device_id, sid, timeout
        self.wrote.append((path, data))
        return {"ok": True, "path": f"/home/owner/{path.removeprefix('$HOME/')}"}


async def test_a_staged_file_lands_beside_the_checkout_and_not_in_it():
    """一份附件落在会话 home 里，检出目录一个字节都没多。

    检出目录是会话 home 的 `room/`，所以「在 home 里」和「不在检出里」是两句话，
    两句都要断言：只断言前一句的话，`room/uploads/x.png` 照样通过。
    """
    machine = Machine()
    project, room = uuid.uuid4(), uuid.uuid4()
    home = device_provider.device_home_dir(project, room)
    landed = await place.write(
        b"bytes",
        home=home,
        name="uploads/abc/图.png",
        hub=machine,
        device_id="device",
        screen="screen",
    )
    (asked, data), = machine.wrote
    assert data == b"bytes"
    assert asked.startswith(f"$HOME/{place.footprint_root()}/")
    assert asked == f"{home}/{place.STAGED_DIR}/uploads/abc/图.png"
    assert f"/{place.CHECKOUT_DIR}/" not in asked.removeprefix("$HOME/")
    # 机器报回来的绝对路径原样交给 agent：后端展不开那台机器的 `$HOME`。
    assert landed == "/home/owner/.cheese/home/{}/{}/attachments/uploads/abc/图.png".format(
        project, room
    )


@pytest.mark.parametrize(
    "home",
    [
        "$HOME/somewhere-else/home/p/r",
        "$HOME/.claude/home/p/r",
        "/absolute/p/r",
        # 检出目录本身——这一条是这条规则的原形：一张图片曾经就落在这里。
        "$HOME/.cheese/home/p/r/room",
    ],
    ids=["outside", "old-root", "absolute", "the-checkout"],
)
async def test_a_write_aimed_outside_the_footprint_is_refused(home):
    """瞄错地方的写不是写进去再说，是当场拒绝。

    落到脚印之外的那一份，`cheese uninstall` 不会收走；落进检出目录的那一份，是别
    人仓库里一个他没加过的未跟踪文件。两种都不在写完之后才发现。
    """
    machine = Machine()
    with pytest.raises(place.OutsideFootprint):
        await place.write(
            b"bytes",
            home=home,
            name="图.png",
            hub=machine,
            device_id="device",
            screen="screen",
        )
    assert machine.wrote == [], "拒绝之后还是写了"
