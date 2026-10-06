"""守卫：「随时 push」在每一轮的系统提示词里（结论 52）。

他的原话是「prompt 里必须有随时 push，包括主 agent 也是」，而这是一条规则而不是
一个默认，所以它的位置就是判据：写在 skill 里，agent 可以读到别的段落就不照做；
写进系统提示词，它每一轮都在场。

为什么非有不可：子 agent 与起它的那个进程同生同死，恢复靠从分支上重派一次（结论
43）。没推上去的提交只活在那台机器的工作树里，机器一回收就跟着没了 —— 「只 commit
的活过不了这台机器」说的就是这一步。

断言取的是**组装出来的那段字**，不是去文件里找这行字：这一条只有出现在
`build_system_prompt` 的返回值里才算数，放在哪个常量里、拼在第几段都不是它。
"""

from app.domain.agent.harness.prompt import UNTITLED_TASK, build_system_prompt


def _assembled(**kwargs) -> str:
    return build_system_prompt("你是芝士。", "", **kwargs)


def test_the_assembled_prompt_tells_the_agent_to_push():
    assert "随时 push" in _assembled()


def test_it_is_there_for_a_room_that_has_nothing_else_in_its_prompt():
    """一个没文档没记忆没角色的会话照样带着它。

    这一条是上面那条的另一半：提示词的每一段都是有条件的，一条无条件的规则最容易
    被拼进某个 `if` 里，而那个 `if` 假的时候没有任何地方会响。
    """
    assert "随时 push" in _assembled(has_doc=False, keeps_memory=False)
    assert "随时 push" in _assembled(has_doc=True, role="后端", keeps_memory=True)


def test_the_naming_ask_is_not_part_of_the_system_prompt():
    """起名是这一轮的事，起完就不该再说；系统提示词在会话里一字不变，所以它不在这里。"""
    assert UNTITLED_TASK not in _assembled(has_doc=True, keeps_memory=True)
