"""会话开场的运行环境：只剩下写一次就一直对的那几条。

这个小节从 #175 来，当时装的是一个**每轮**的抬头：时间预算、磁盘、验收卡。三样
后来都不该在这儿了——倒计时根本不存在（说有会让 agent 赶工），卡和磁盘是同一个
commit 造的 `cheese_status` 一次调用的事，而抬头只是把某一轮的快照冻进了整个会
话。剩下的两条是任何调用和任何一轮都重建不出来的：这台机器多大，以及上一轮做到
哪了。

会变的东西不在这里——它们在变的那一刻写成平台提醒，跟着下一轮的消息进来。
"""

from app.domain.agent.chat import _resume_notice, _session_opening_lines
from app.domain.agent.harness.prompt import build_system_prompt


def test_the_machines_size_is_stated_when_the_backend_knows_it() -> None:
    """An agent cannot read its own cgroup limit. Without being told, a build the
    kernel OOM-kills reads as a broken toolchain rather than a small box — and
    the usual reaction, retuning --max-old-space-size, cannot help, because V8
    just climbs until the cgroup kills it again."""
    joined = "\n".join(_session_opening_lines(sandbox=(2048, 2)))

    assert "2GB" in joined
    assert "2 核" in joined
    assert "OOM" in joined


def test_a_backend_that_does_not_know_its_size_says_nothing() -> None:
    """An enrolled machine belongs to someone else and the platform does not set
    its limits. Inventing a number there would be worse than staying quiet: the
    agent would skip work it could actually have done."""
    joined = "\n".join(_session_opening_lines())

    assert "内存" not in joined
    assert "OOM" not in joined


def test_a_fractional_gigabyte_is_not_rounded_to_a_lie() -> None:
    """512m must not print as '1GB' — the number is only useful if an agent can
    weigh a command against it."""
    assert "1.5GB" in "\n".join(_session_opening_lines(sandbox=(1536, 1)))


def test_a_machine_that_is_not_ours_states_no_size() -> None:
    """Asked of the backend the pool hands the turn, not of a class — the answer
    has to survive every layer between the machine and the prompt."""
    from app.domain.agent.chat import _sandbox_limits
    from app.domain.agent.compute import build_compute_pool

    pool = build_compute_pool()

    assert _sandbox_limits(pool.select(provider_id="device")) is None


def test_the_section_is_absent_when_there_is_nothing_to_open_with() -> None:
    with_lines = build_system_prompt("base", "", None, [], session_opening=["- x"])
    assert "## 这个会话开场时的运行环境" in with_lines

    assert "## 这个会话开场时的运行环境" not in build_system_prompt(
        "base", "", None, []
    )


def test_no_countdown_is_ever_claimed() -> None:
    """turn 活跃度检测 (2026-08-09): a fixed minute budget or 「到点会被中断」 was
    observed making the agent rush against what is only a wedged-turn safety
    net. The line that used to deny one is gone too — a session that was never
    told about a deadline does not need to be told there isn't one."""
    joined = "\n".join(_session_opening_lines(sandbox=(2048, 2), progress=[]))

    assert "分钟" not in joined
    assert "到点会被中断" not in joined
    assert "倒计时" not in joined


def test_what_a_session_opens_with_says_nothing_about_this_particular_turn() -> None:
    """The one per-turn fact left — this turn continues a dead one — travels
    with the turn, not with the session: a session serves many turns and only
    some of them are continuations."""
    joined = "\n".join(_session_opening_lines(sandbox=(2048, 2)))

    assert "本轮" not in joined
    assert "本轮接着上一轮跑" in _resume_notice()
