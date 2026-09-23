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
    harness="claude-code",
)
OPENING = Opening(system_prompt="CONTRACT")
LABEL = thread_label(uuid.UUID("00000000-0000-4000-8000-000000000003"))
#: 同一条会话里的第二条活，用来看「停掉一条」停的是不是只有那一条。
SIBLING = thread_label(uuid.UUID("00000000-0000-4000-8000-000000000004"))

#: 一句答案里反引号引起来的东西。
_CITED = re.compile(r"`([A-Za-z0-9_./]+)`")
#: 一条路径长什么样：带 ``/``，或者以一个文件扩展名结尾。从 ``app/domain/`` 起算。
#: 不止 ``.py``——一条要求的做法写在哪儿就引哪儿，``agent/skill_library/`` 下发给
#: agent 的那几份说明也是本仓库的东西，也核得了。
_PATH = re.compile(r"/|\.(?:py|md)\Z")
#: 一个符号名长什么样：``SubThreads``、``thread_label``、``AgentRuntime.deliver``。
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def _participates(part: str, src: str) -> bool:
    """这个名字在这份源码里**参与了代码**，而不只是躺在一张字面量清单里。

    「文件里搜得到这串字」是不够的，而且不够的方式恰好是反着的：
    ``device_provider.py`` 里那三个模型别名只出现在一张 ``merged.pop`` 的删除名单
    里——文件里搜得到，而它证明的是那句话的反面。所以这里认的是出现的**位置**：
    被定义、被赋值、被当成键写进去、被调用、被取属性、从 payload 里被读出来。
    """
    p = re.escape(part)
    shapes = (
        rf"(?:def|class)\s+{p}\b",  # 定义在这儿
        rf"\b{p}\b\s*(?::[^=\n]+)?=(?!=)",  # 赋值（含带注解的）
        rf"[\"']{p}[\"']\s*\]\s*=(?!=)",  # env["X"] = ...
        rf"[\"']{p}[\"']\s*:",  # 字面量里的键 "X": ...
        rf"\b{p}\s*\(",  # 调用
        rf"\.{p}\b",  # 取属性
        rf"get\(\s*[\"']{p}[\"']",  # 从 payload 里读这个字段
    )
    return any(re.search(shape, src) for shape in shapes)


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
    ``SubThreads``、``thread_label``、``AgentRuntime.deliver``——而删掉一个符号比搬
    走一个文件常见得多：文件照样在，这句话已经是假的了，读起来却和真的一模一样。

    而且核的是那个名字**出现在什么位置**（``_participates``），不是文件里搜不搜得
    到它。搜得到就算数的话，一张删除名单也算数：那正是这条守卫想挡的「读起来和真
    的一模一样」，只不过它读起来和真的一模一样的同时，说的是反话。
    """
    answer = HARNESSES[name].subagents[requirement]
    cited = _CITED.findall(answer)
    paths = [c for c in cited if _PATH.search(c)]
    assert paths, f"{name}/{requirement} 没有指出这件事写在哪个文件里"
    missing = [path for path in paths if not (DOMAIN / path).exists()]
    assert not missing, f"{name}/{requirement} 指着不存在的文件：{missing}"

    sources = [(DOMAIN / path).read_text(encoding="utf-8") for path in paths]
    for symbol in (c for c in cited if c not in paths and _SYMBOL.match(c)):
        for part in symbol.split("."):
            found = any(_participates(part, src) for src in sources)
            assert found, (
                f"{name}/{requirement} 指着 `{symbol}`，但 {paths} 里没有一处真的"
                f"定义、赋值或读 {part}——这句话此刻已经不成立了"
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


def _stream(runtime: ContractHarness) -> list[tuple[str, str | None]]:
    backlog = runtime.backlog(SESSION)
    return [
        (e.text, e.thread_label)
        for entry in backlog.unread()
        for e in backlog.assemble(entry)
    ]


async def test_a_parent_thread_spawns_a_worker_and_names_its_model() -> None:
    """跑的是被指定的那个模型，从它干出来的活上读。

    断言 ``worker.model == "opus"`` 只是把构造参数读回来：一个收下模型名再去跑另
    一个模型的骨架照样绿，而那正是这条要求要挡掉的东西。所以模型和指令一样，读者
    是 ``works()``——它说出来的那句活里带着当前跑的是哪个。
    """
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    assert worker.running
    assert runtime.workers(SESSION) == [worker]

    worker.works()
    assert _stream(runtime) == [("在做（opus）：查分页", LABEL)]


async def test_a_worker_spawned_without_a_model_is_not_spawned_at_all() -> None:
    """「并指定模型」的另一半：不说跑哪个，就不是一条起得出来的子线程。

    上一条守「起出来的跑的是被指定的那个」，这一条守「必须指定」。两条都要在，因
    为它们各自能单独变假：只有上一条时，不给模型可以悄悄落到一个默认值上。
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

    assert _stream(runtime) == [
        ("我来看看", None),
        ("查到了", LABEL),
        ("顺带还有一处", LABEL),
    ]


async def test_a_parent_thread_retasks_its_worker() -> None:
    """人对卡的操作投递给父线程执行，改指令的是父线程自己。

    断在它**干出来的活**上，不是断在那个字段上：``worker.instruction`` 读回刚写进
    去的那句话，证明的只是 ``retask`` 是个 setter——把那次赋值删掉，这条断言之外
    的一切照绿。所以这里读的是换了要求之后它干的是哪件事，而旧那件事再也出不来；
    标识一路不变，「还是那条活、还归那张卡」也是从同一条流上读出来的。
    """
    runtime = await _session()
    worker = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    worker.works()

    # 送到的是父线程，不是那条子线程（结论 43）。
    assert await runtime.deliver(SESSION.topic_id, "先只改后端") is True
    (instruction,) = runtime.delivered(SESSION)
    # 父线程读到它，自己去改子线程的指令。
    worker.retask(instruction)
    assert worker.instruction == "先只改后端"
    worker.works()

    assert _stream(runtime) == [
        ("在做（opus）：查分页", LABEL),
        ("在做（opus）：先只改后端", LABEL),
    ]


async def test_a_parent_thread_stops_one_worker_and_leaves_the_others_running() -> None:
    """停的是**一条**子线程，和改指令一样是父线程自己那一手（结论 43）。

    负向对照就是那条同胞：拿会话级的停（``interrupt`` / ``close``）来答这一条，同
    一条会话里的两条 worker 会一起停——这条断言红。而那是结论 43 的另一句「子
    agent 与父进程同生同死」，拿它来答这一条，这条要求就恒真：任何一个会话杀得掉
    的骨架都通过，「停不掉单条子线程」那一档正好被放行。

    另一半是停掉之后什么都做不了：一个停不住、还在往房间里写的 worker，在时间线
    上和没停是同一个样子。
    """
    runtime = await _session()
    stopped = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    sibling = runtime.spawn(SESSION, label=SIBLING, model="opus", instruction="写用例")

    stopped.stop()

    assert not stopped.running
    with pytest.raises(SubagentStopped):
        stopped.says("我还在说")
    # 停掉的子线程也接不了新指令。
    with pytest.raises(SubagentStopped):
        stopped.retask("再改一版")

    # 同胞照跑，会话照在。
    assert sibling.running
    sibling.works()
    assert runtime.workers(SESSION) == [stopped, sibling]
    assert runtime.holds(SESSION.topic_id)
    assert _stream(runtime) == [("在做（opus）：写用例", SIBLING)]


async def test_stopping_the_session_is_a_different_sentence() -> None:
    """``interrupt`` 停的是整条会话的活，不是某一条子线程。

    这一条在这里，是为了让上一条没法拿它蒙混过去：两条 worker 一起停，说明这个动
    词的粒度是会话。``interrupt`` 比 ``close`` 弱，会话还在，下一个 send 接着走。
    """
    runtime = await _session()
    one = runtime.spawn(SESSION, label=LABEL, model="opus", instruction="查分页")
    two = runtime.spawn(SESSION, label=SIBLING, model="opus", instruction="写用例")

    assert await runtime.interrupt(SESSION) is True

    assert not one.running
    assert not two.running
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
