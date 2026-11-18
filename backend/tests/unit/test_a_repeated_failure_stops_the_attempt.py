"""守卫：同一个做法反复失败要停下换做法，落在动手干活的会话每一轮的系统提示词里。

2026-10-24 的一次会话：沙箱里没有 C++ 编译器，为了在本地验证一份模板连做了 28 次
命令行尝试，其中十来次都在补「缺哪个共享库」，每补一个又冒出一个新的，上下文里堆了
近十万字符的编译报错，最后一次失败之后模型生成跑偏。负责人 2026201782 拍了这条：
不是平台问题，是模型太犟，得有一条规矩告诉它别这么犟。

断言的是清单（`prompts.PROFILES`）：任务会话在任何事实组合下都读到这一篇，读不到
主会话系统提示词的分身也拿到同一篇。这句话怎么写由负责人定，不在这里断言。
"""

import itertools

import pytest

from app.domain.prompts import SUBAGENT_RULES, Facts, Kind, piece, selected


@pytest.mark.parametrize(
    ("working_directory", "has_doc", "memory_synced", "role"),
    itertools.product((True, False), (False, True), (False, True), (None, "后端")),
)
def test_a_task_session_reads_the_rule_whatever_else_it_has(
    working_directory, has_doc, memory_synced, role
):
    """提示词的每一段都是有条件的，而无条件的规则最容易被拼进某个条件里 ——
    那个条件假的时候没有任何地方会响。"""
    facts = Facts(
        working_directory=working_directory,
        has_doc=has_doc,
        memory_synced=memory_synced,
        role=role,
    )
    assert "system/repeated_failure" in selected(Kind.task, facts)


def test_a_subagent_reads_the_same_rule():
    """连着跑命令、干重活的常常是分身，而分身读不到会话的系统提示词。"""
    assert piece("system/repeated_failure") in SUBAGENT_RULES
