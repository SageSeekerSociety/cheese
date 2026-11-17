"""守卫：「随时 push」在做任务的会话每一轮的系统提示词里（结论 52）。

他的原话是「prompt 里必须有随时 push，包括主 agent 也是」，而这是一条规则而不是
一个默认，所以它的位置就是判据：写在 skill 里，agent 可以读到别的段落就不照做；
写进系统提示词，它每一轮都在场。

为什么非有不可：子 agent 与起它的那个进程同生同死，恢复靠从分支上重派一次（结论
43）。没推上去的提交只活在那台机器的工作树里，机器一回收就跟着没了。

断言的是任务那一种会话的清单（`prompts.PROFILES`）在任何事实组合下都带着这一篇，
不是这句话怎么写。
"""

import itertools

import pytest

from app.domain.prompts import Facts, Kind, selected

HARNESSES = (True, False)


@pytest.mark.parametrize(
    ("working_directory", "has_doc", "memory_synced", "role"),
    itertools.product(HARNESSES, (False, True), (False, True), (None, "后端")),
)
def test_a_task_session_is_told_to_push_whatever_else_it_has(
    working_directory, has_doc, memory_synced, role
):
    """提示词的每一段都是有条件的，一条无条件的规则最容易被拼进某个条件里，而那个
    条件假的时候没有任何地方会响。"""
    facts = Facts(
        working_directory=working_directory,
        has_doc=has_doc,
        memory_synced=memory_synced,
        role=role,
    )
    assert "system/always_push" in selected(Kind.task, facts)


@pytest.mark.parametrize("kind", list(Kind))
def test_the_naming_ask_is_not_part_of_any_system_prompt(kind):
    """起名是这一轮的事，起完就不该再说；系统提示词在会话里一字不变，所以它不在这里。"""
    facts = Facts(has_doc=True, memory_synced=True)
    assert "notices/untitled_task" not in selected(kind, facts)
