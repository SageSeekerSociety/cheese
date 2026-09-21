"""骨架契约的四条硬性要求，对注册表里的每个骨架各跑一遍（结论 43）。

派一条活是 agent 对骨架原生 subagent 的工具调用，不走平台。那条路要成立，骨架得
答得出四件事：起得了子 agent 并指定模型、子 agent 的事件带可归到卡的线程标识、父
线程改得了它的指令、父线程停得掉它。

**为什么这条验收永远跑得完**：四条是硬性要求，``Difference`` 里不许有一条码描述它
们中的任何一项，所以答不出的骨架不在 ``HARNESSES`` 里——要跑的名单就是注册表，而
注册表里每一条都答得出。这不是循环论证，是那次产品收缩的可判形式：收缩发生在构造
``Harness`` 的那一刻（``Harness.__post_init__``），这里只是把它读出来。

四条动作长什么样，由 ``ContractHarness`` 加 ``FakeSubagent`` 说；一个真骨架用自己
的记录回答的是同一批事，在 ``tests/fixtures/harness-contract/`` 的
``a-subagent-*.json`` 里，由 ``test_harness_contract.py`` 跑。
"""

import re
import uuid
from pathlib import Path

import pytest

from app.domain.agent.capability import Difference
from app.domain.agent.harness import (
    CLAUDE_CODE,
    CODEX,
    HARNESSES,
    PI,
    Harness,
    Opening,
    SessionRef,
    SubagentRequirement,
)
from app.domain.room_task.thread_label import thread_label
from tests.support.contract_harness import ContractHarness
from tests.support.fake_subagent import SubagentStopped

BACKEND = Path(__file__).resolve().parents[2]
DOMAIN = BACKEND / "app/domain"
HARNESS_PACKAGE = DOMAIN / "agent/harness"

SESSION = SessionRef(
    project_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    topic_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
)
OPENING = Opening(system_prompt="CONTRACT")
LABEL = thread_label(uuid.UUID("00000000-0000-4000-8000-000000000003"))

#: 一句答案里反引号引起来的东西。以 ``.py`` 结尾的是路径（从 ``app/domain/`` 起
#: 算），其余的是符号名。
_CITED = re.compile(r"`([A-Za-z0-9_./]+)`")
#: 一个符号名长什么样：``SubThreads``、``thread_label``、``AgentRuntime.deliver``。
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def _every_answer() -> list[tuple[str, SubagentRequirement]]:
    return [
        (name, requirement)
        for name in sorted(HARNESSES)
        for requirement in SubagentRequirement
    ]


# --- 注册表里的每个骨架，四条各答一遍 ----------------------------------------


@pytest.mark.parametrize(("name", "requirement"), _every_answer(), ids=str)
def test_a_running_harness_answers_every_hard_requirement(
    name: str, requirement: SubagentRequirement
) -> None:
    answer = HARNESSES[name].subagents[requirement]
    assert answer.strip()
    # 一条差异码不是答案。``Difference`` 是 StrEnum，所以「是不是一个 str」拦不住
    # 它——认的是值本身。
    assert answer not in set(Difference), f"{name}/{requirement} 填的是一条差异码"


@pytest.mark.parametrize(("name", "requirement"), _every_answer(), ids=str)
def test_an_answer_points_at_code_that_exists(
    name: str, requirement: SubagentRequirement
) -> None:
    """一句「已支持」指不出是哪一行做的，下一个人没有办法核，也没有办法在它失效的
    时候发现——和功能矩阵里那些格子同一条规矩。

    核到符号那一层，不是只核文件在不在。一句答案的实质是里面那几个名字——
    ``SubThreads``、``thread_label``、``AgentRuntime.deliver``、启动环境里那三个模
    型别名——而删掉一个符号比搬走一个文件常见得多：文件照样在，这句话已经是假的
    了，读起来却和真的一模一样。
    """
    answer = HARNESSES[name].subagents[requirement]
    cited = _CITED.findall(answer)
    paths = [c for c in cited if c.endswith(".py")]
    assert paths, f"{name}/{requirement} 没有指出代码在哪个文件里"
    missing = [path for path in paths if not (DOMAIN / path).exists()]
    assert not missing, f"{name}/{requirement} 指着不存在的文件：{missing}"

    sources = [(DOMAIN / path).read_text(encoding="utf-8") for path in paths]
    for symbol in (c for c in cited if not c.endswith(".py") and _SYMBOL.match(c)):
        for part in symbol.split("."):
            found = any(re.search(rf"\b{re.escape(part)}\b", src) for src in sources)
            assert found, (
                f"{name}/{requirement} 指着 `{symbol}`，但 {paths} 里没有 {part}——"
                "这句话此刻已经不成立了"
            )


def test_a_harness_that_cannot_answer_cannot_be_built() -> None:
    """「答不出就摘掉」的可判形式。

    判在构造上：注册表是一个字面量，而一个造得出来的条目总会有人写进去。
    """
    answers = dict(HARNESSES[CLAUDE_CODE].subagents)
    for requirement in SubagentRequirement:
        short = {k: v for k, v in answers.items() if k is not requirement}
        with pytest.raises(ValueError, match="硬性要求"):
            Harness("half-answered", "半个", subagents=short)


def test_a_difference_code_is_not_an_answer() -> None:
    """结论 43 不许给这四条填差异码。填了就造不出来，而不是造出一个「暂缺」的骨架。

    ``Difference`` 是 StrEnum，一条码放进 ``Mapping[..., str]`` 里类型是对的，所以
    这条守卫是真的在挡东西，不是在复述类型注解。
    """
    answers = dict(HARNESSES[CLAUDE_CODE].subagents)
    answers[SubagentRequirement.LABELS_ITS_THREAD] = Difference.NOT_BUILT_IN
    with pytest.raises(ValueError, match="硬性要求"):
        Harness("coded", "填了码的", subagents=answers)


def test_a_harness_that_is_not_registered_still_has_its_code() -> None:
    """摘掉的是注册，不是代码（结论 43）。

    两个方向都断言，但只断言这两个骨架：代码还在而注册没了，才是那次产品收缩本
    身；哪天谁把适配层也删了，这里红，因为那是另一个决定。把整张注册表钉成等号是
    另一回事——将来多一个答得出四条的骨架，那是这条回路走通了，不是回归。
    """
    assert CODEX not in HARNESSES
    assert PI not in HARNESSES
    for name in (CODEX, PI):
        assert (HARNESS_PACKAGE / name.replace("-", "_") / "behaviour.py").exists()


# --- 四条动作各自长什么样 ----------------------------------------------------


async def _session() -> ContractHarness:
    runtime = ContractHarness()
    await runtime.ensure(SESSION, OPENING)
    return runtime


async def test_a_parent_thread_spawns_a_worker_and_names_its_model() -> None:
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    assert worker.running
    assert runtime.workers(SESSION) == [worker]


async def test_a_worker_spawned_without_a_model_is_not_spawned_at_all() -> None:
    """「并指定模型」那半，要有一天能红。

    断言 ``worker.model == "opus"`` 只是把构造参数读回来：那个字段再没有第二个读
    者，谁也不校验它，把它从那次调用里删掉测试照绿。所以不指定模型这件事本身就是
    个错——一条起不出来的子线程，而不是一条跑着不知道什么模型的子线程。
    """
    runtime = await _session()
    with pytest.raises(ValueError, match="模型"):
        runtime.spawn(SESSION, label=LABEL, model="", instruction="查分页")
    assert runtime.workers(SESSION) == []


async def test_everything_a_worker_says_carries_its_thread_label() -> None:
    """归属是**读出来的**：谁也没有报过一次绑定，而这条子线程说的每一句都带着标识
    出来，主线程说的不带。"""
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    await runtime.send(
        SESSION, "我来看看", OPENING, work_id=uuid.uuid4(), on_mark=lambda _: None
    )
    worker.says("查到了")
    worker.says("顺带还有一处")

    backlog = runtime.backlog(SESSION)
    events = [e for entry in backlog.unread() for e in backlog.assemble(entry)]
    assert [(e.text, e.thread_label) for e in events] == [
        ("我来看看", None),
        ("查到了", LABEL),
        ("顺带还有一处", LABEL),
    ]


async def test_a_parent_thread_retasks_its_worker() -> None:
    """人对卡的操作投递给父线程执行，改指令的是父线程自己。

    断在看得见的那一侧：``worker.instruction`` 读回刚写进去的那句话，证明的只是那
    个方法是个 setter。换了要求之后这条子线程还在说话、说的话还带着同一个标识出
    来——「还是那条活、还归那张卡」是这么读出来的。
    """
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")

    # 送到的是父线程，不是那条子线程（结论 43）。
    assert await runtime.deliver(SESSION.topic_id, "先只改后端") is True
    (instruction,) = runtime.delivered(SESSION)
    # 父线程读到它，自己去改子线程的指令。
    worker.retask(instruction)
    worker.says("改完了")

    backlog = runtime.backlog(SESSION)
    events = [e for entry in backlog.unread() for e in backlog.assemble(entry)]
    assert [(e.text, e.thread_label) for e in events] == [("改完了", LABEL)]


async def test_a_parent_thread_stops_its_worker() -> None:
    """停掉之后说不出话来。一个停不住、还在往房间里写的 worker，在时间线上和没停
    是同一个样子。"""
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")

    assert await runtime.interrupt(SESSION) is True

    assert not worker.running
    with pytest.raises(SubagentStopped):
        worker.says("我还在说")
    # 停掉的子线程也接不了新指令——负向对照：停不住的话，改指令会照常成功。
    with pytest.raises(SubagentStopped):
        worker.retask("再改一版")
    # interrupt 比 close 弱：会话还在，下一个 send 接着走。
    assert runtime.holds(SESSION.topic_id)


async def test_a_worker_dies_with_the_parent_session() -> None:
    """子 agent 与父进程同生同死（结论 43）：恢复靠从分支重派，不靠把 worker 捞
    回来。"""
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")

    await runtime.close(SESSION)

    assert not worker.running
    assert not runtime.holds(SESSION.topic_id)
    assert runtime.workers(SESSION) == []
