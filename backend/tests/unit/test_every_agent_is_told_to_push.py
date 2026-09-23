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

from app.domain.agent.harness.prompt import build_system_prompt


def _assembled(**kwargs) -> str:
    return build_system_prompt("你是芝士。", "", None, [], **kwargs)


def test_the_assembled_prompt_tells_the_agent_to_push():
    assert "随时 push" in _assembled()


def test_it_is_there_for_a_room_that_has_nothing_else_in_its_prompt():
    """一个刚建出来、没名字没文档没记忆没名册的房间照样带着它。

    这一条是上面那条的另一半：提示词的每一段都是有条件的，一条无条件的规则最容易
    被拼进某个 `if` 里，而那个 `if` 假的时候没有任何地方会响。
    """
    assert "随时 push" in _assembled(untitled=True)
    assert "随时 push" in _assembled(role="后端", untitled=False)


def test_the_naming_block_is_only_here_while_the_topic_is_unnamed():
    named = _assembled(untitled=False)
    unnamed = _assembled(untitled=True)

    assert "本轮第一件事：先给本话题起名" not in named
    assert "本轮第一件事：先给本话题起名" in unnamed


def test_naming_comes_before_the_push_rule():
    """起名块自己写着「先于一切」——排不到前面，那段话就是空头支票。"""
    unnamed = _assembled(untitled=True)

    assert unnamed.index("先给本话题起名") < unnamed.index("随时 push")
