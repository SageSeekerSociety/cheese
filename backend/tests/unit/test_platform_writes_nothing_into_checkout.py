"""平台不因为自己的东西改被托管的仓库（结论 49，不变量 I21b）。

被托管的仓库里只能有 agent 替用户做的活。平台的配置、钩子、技能、系统提示词、
进度、记忆、实况文档、资料库、草稿、备份，一样都不进项目的 git 树，**也不以未跟
踪文件的形式落在检出目录里**——后半句才是难的那半句：一个没人 `git add` 的文件不
会出现在任何提交里，它只是永远待在别人的 `git status` 里，而删掉它的人不是我们。

这条规则的可判形式不是「扫一遍全仓，看有没有哪个写文件的调用的目标路径以检出目
录开头」。那是跨过程的路径前缀分析：路径在运行时拼出来，AST 看不见，而真正写下
去的那一步在另一台机器上。换成两条能判的：

① 把服务端手上的字节当成一个文件送上机器的调用，`app/` 里只有一处，就是
   `place.write`；
② `place.write` 自己断言目标落在 `footprint_root()` 之下、且不在检出目录里。

两条合起来说的是同一件事：送字节的入口只有一个，那一个自己守着前缀。

**这两条守不住的那一半，得说在明处**：平台还会把自己的程序用 `hub.exec` 送上
机器——启动脚本、钩子转发器、每轮轮换的 forwarded token、工具链和 `cheese` CLI，
都是一段 shell 里的 `cat >` / `printf %s >`。它们写的是 `footprint_root()` 之下
平台自己的文件，结论 49 允许；但目标路径是运行时拼进 shell 字符串里的，AST 数不
到，把 `hub.exec` 的十几个调用点全数进来再白名单，只会让每加一条无关的 exec 都变
红。所以那一半交给「跑完真一轮再读检出目录的 porcelain 状态」那条 acceptance
（`scripts/remote_execution/private_terminal.py --ordinary`），不交给这里。
"""

import ast
import asyncio
import base64
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
    """**请求**一次「把这串字节写成机器上的一个文件」的那些行。

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
            for key, value in zip(payload.keys, payload.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "subtype"
                    and isinstance(value, ast.Constant)
                    and value.value == STAGE_FILE
                ):
                    lines.append(node.lineno)
    return lines


def test_only_one_call_in_the_app_ships_file_bytes_to_a_machine():
    """送字节的调用点数 == 1，而那一处在 `place.py` 里。

    数调用点，而不是读路径：路径是运行时拼的，调用点不是。一个新的写点在这里是一
    条失败的断言，在生产上是一张没人能解释的未跟踪文件——它在别人的仓库里，而看见
    它的人不知道它是谁放的，也不知道删掉会不会弄坏什么。

    数的是 `file.put` 与 `stage_file` 这两条**文件字节**的通路，不是「所有能让机器
    上多出一个文件的办法」——后者包括 `hub.exec` 里那些 `cat >`，见模块 docstring。
    """
    found: dict[str, list[int]] = {}
    for source in APP.rglob("*.py"):
        if source.name == "place.py" and source.parent.name == "agent":
            continue
        lines = call_sites(ast.parse(source.read_text()))
        if lines:
            found[str(source.relative_to(APP))] = lines
    assert found == {}, (
        f"这些地方直接把文件字节送上机器，绕过了 place.write 的前缀断言：{found}"
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
        self.budget: float | None = None

    async def put_file(self, device_id, sid, path, data, *, timeout=30):
        del device_id, sid
        self.budget = timeout
        self.wrote.append((path, data))
        return {"ok": True, "path": f"/home/owner/{path.removeprefix('$HOME/')}"}


class Executor:
    """记下自己收到过哪些 control 请求的一个执行器。"""

    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def control(self, target, payload, *, hub=None, trace_id=None):
        del target, hub, trace_id
        self.asked.append(payload)
        return {"ok": True, "path": f"/root/.cheese/{payload['path']}"}


@pytest.fixture
def executor(monkeypatch):
    """把执行器那条通路接到一个记事本上，同时盯住它有没有被走。"""
    from app.domain.agent import private_chat

    spy = Executor()
    monkeypatch.setattr(private_chat, "control", spy.control)
    return spy


async def test_a_staged_file_lands_beside_the_checkout_and_not_in_it(executor):
    """一份附件落在会话 home 里，检出目录一个字节都没多。

    检出目录是会话 home 的 `room/`，所以「在 home 里」和「不在检出里」是两句话，
    两句都要断言：只断言前一句的话，`room/uploads/x.png` 照样通过。

    没有执行器的屏幕走连接器，拿到的是 `$HOME` 锚定的绝对路径——这是下面那条
    执行器用例的负向对照：两条通路都走的话，这里的 `executor.asked` 会非空。
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
    assert executor.asked == [], "没有执行器的屏幕不该去问执行器"
    ((asked, data),) = machine.wrote
    assert data == b"bytes"
    assert asked.startswith(f"$HOME/{place.footprint_root()}/")
    assert asked == f"{home}/{place.STAGED_DIR}/uploads/abc/图.png"
    assert f"/{place.CHECKOUT_DIR}/" not in asked.removeprefix("$HOME/")
    # 机器报回来的绝对路径原样交给 agent：后端展不开那台机器的 `$HOME`。
    owner_home = f"/home/owner/.cheese/home/{project}/{room}"
    assert landed == f"{owner_home}/attachments/uploads/abc/图.png"


async def test_a_screen_with_an_executor_is_written_through_it(executor):
    """有执行器的屏幕，字节只走执行器，而且走的是相对路径那一份。

    两条通路解的不是同一个 `$HOME`：连接器是机器主人的，执行器是会话自己的。送错
    那一份不会报错——它在一个真实目录里写下一个真实文件，只是没有人会去看。私聊的
    执行器是个容器，写在它坐的那台宿主机上的文件，agent 根本打不开。

    所以这里盯三件事：连接器一次都没被调（不是两条都走的双写）、执行器收到的是
    `attachments/<name>` 这个相对路径、返回值是执行器报回来的那个绝对路径原样。
    """
    machine = Machine()
    home = device_provider.device_home_dir(uuid.uuid4(), uuid.uuid4())
    landed = await place.write(
        b"bytes",
        home=home,
        name="uploads/abc/图.png",
        hub=machine,
        device_id="device",
        screen="screen",
        execution_target={"kind": "private", "home": home},
    )
    assert machine.wrote == [], "有执行器还走了连接器，就是它替掉的那份双写"
    (payload,) = executor.asked
    assert payload["subtype"] == STAGE_FILE
    assert payload["path"] == f"{place.STAGED_DIR}/uploads/abc/图.png"
    assert not payload["path"].startswith("$HOME"), "执行器要的是相对它自己 home 的"
    assert base64.b64decode(payload["data"]) == b"bytes"
    assert landed == "/root/.cheese/attachments/uploads/abc/图.png"


async def test_the_callers_budget_holds_on_both_transports(monkeypatch):
    """`timeout` 是调用方的预算，两条通路都归它管。

    唯一的调用者给的是 20 秒（`device_provider._FILE_STAGE_TIMEOUT_S`），理由写在
    那个常量上：过了这个点消息就不带这张图继续走，所以等待的代价是读的人付的。执
    行器那条通路调的 `private_chat.control` 自己没有 timeout 参数，里面写死的是
    660 秒——一轮活的合理上限，一张图片的十一分钟。图片是一张一张送的，于是没有这
    条断言，一个卡住的执行器能把一条消息按住「每张图各一次」那么久。

    一个收下参数却只对一条通路生效的入口，比没有这个参数更坏：调用方读到的是它已
    经定好了上限。
    """
    machine = Machine()
    home = device_provider.device_home_dir(uuid.uuid4(), uuid.uuid4())
    await place.write(
        b"bytes",
        home=home,
        name="图.png",
        hub=machine,
        device_id="device",
        screen="screen",
        timeout=7,
    )
    assert machine.budget == 7

    from app.domain.agent import private_chat

    async def never_answers(target, payload, *, hub=None, trace_id=None):
        del target, payload, hub, trace_id
        await asyncio.Event().wait()

    monkeypatch.setattr(private_chat, "control", never_answers)
    with pytest.raises(TimeoutError):
        await place.write(
            b"bytes",
            home=home,
            name="图.png",
            hub=machine,
            device_id="device",
            screen="screen",
            execution_target={"kind": "private", "home": home},
            timeout=0.01,
        )


@pytest.mark.parametrize(
    "home",
    [
        "$HOME/somewhere-else/home/p/r",
        "$HOME/.claude/home/p/r",
        "/absolute/p/r",
        # 检出目录本身——这一条是这条规则的原形：一张图片曾经就落在这里。
        "$HOME/.cheese/home/p/r/room",
        # 检出目录就贴在脚印根下面：它前面没有斜杠，所以「`/room/` 在不在里面」这
        # 种读法会当场放过它。断言要挡的是这个名字作为**一段**出现，不是它出现在
        # 第二段以后。
        "$HOME/.cheese/room/p/r",
    ],
    ids=["outside", "old-root", "absolute", "the-checkout", "checkout-first"],
)
async def test_a_write_aimed_outside_the_footprint_is_refused(home, executor):
    """瞄错地方的写不是写进去再说，是当场拒绝。

    落到脚印之外的那一份，`cheese uninstall` 不会收走；落进检出目录的那一份，是别
    人仓库里一个他没加过的未跟踪文件。两种都不在写完之后才发现。

    断言在通路之前：拒绝发生在选连接器还是选执行器之前，所以两边都得一个字节没
    收到——只盯住连接器的话，把检出目录传给一个有执行器的屏幕照样能写进去。
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
            execution_target={"kind": "private", "home": home},
        )
    assert machine.wrote == [], "拒绝之后还是写了"
    assert executor.asked == [], "拒绝之后还是让执行器写了"
