"""守卫：同一个做法反复失败要停下换做法，落在每一轮的系统提示词里。

2026-10-24 的一次会话：沙箱里没有 C++ 编译器，为了在本地验证一份模板连做了 28 次
命令行尝试，其中十来次都在补「缺哪个共享库」，每补一个又冒出一个新的，上下文里堆了
近十万字符的编译报错，最后一次失败之后模型生成跑偏。负责人 2026201782 拍了这条：
不是平台问题，是模型太犟，得有一条规矩告诉它别这么犟。

断言取的是 `build_system_prompt` 组装出来的那段字，不是去文件里找常量：这条只有真
进到每一轮的提示词里才算数，写在哪个常量、拼在第几段都不是它（同
`test_every_agent_is_told_to_push.py` 的理由）。

**两半都要在**，这是这一条和「别重试」的分界，也是负责人明确否掉的那种写法：试一次
才知道做不做得出，编译器没装、去装一个本来是对的。所以一半是还值得试的两种情形，
一半是认出「没有新信息」就该停。只留一半的句子会读成禁令，而它禁掉的正是正常的尝试。
"""

from app.domain.agent.harness.prompt import REPEATED_FAILURE, build_system_prompt


def _assembled(**kwargs) -> str:
    return build_system_prompt("你是芝士。", "", **kwargs)


def test_the_rule_is_in_the_assembled_prompt():
    assert REPEATED_FAILURE in _assembled()


def test_it_is_there_for_a_room_that_has_nothing_else_in_its_prompt():
    """提示词的每一段都是有条件的，而无条件的规则最容易被拼进某个 `if` 里 ——
    那个 `if` 假的时候没有任何地方会响。"""
    assert REPEATED_FAILURE in _assembled(has_doc=False, keeps_memory=False)
    assert REPEATED_FAILURE in _assembled(has_doc=True, role="后端", keeps_memory=True)


def test_the_rule_keeps_the_attempts_that_are_still_worth_making():
    """「换做法、换依赖、换条路试」和「间歇性的失败」要留在句子里。删掉它们，这条
    就退化成负责人否掉的「别重试」——而那会把正常的尝试一起禁掉。

    读的是组装出来的那段字，不是常量：这条规矩要到得了 agent 手里才成立，而只断言
    常量会让「定义了却没接进提示词」照绿。"""
    text = _assembled()
    assert "换做法" in text
    assert "换依赖" in text
    assert "间歇" in text


def test_the_rule_does_not_read_as_filling_gaps_being_wrong():
    """缺什么补什么不是错，一口一口补但心里有清单是正常的。负责人 2026201782 纠正过
    这一句：第一版写「补一个缺口又冒出下一个，就该停」，读起来像在否定补齐本身。

    该停的是每轮只看到「下一个」缺口、看不到还缺多少——那就没法知道这条路通不通。
    所以这一半要同时说到「看不到还缺多少」和「摸清」：只说前者会读成「别再补了」，
    只说后者会读成「补之前先列清单」，都不是要说的那件事。"""
    text = _assembled()
    assert "看不到还缺多少" in text
    assert "摸清" in text


def test_the_rule_names_what_stopping_looks_like():
    """停下不是什么都不做，是两个具体的出口之一：换个做法，或者把东西交出去、
    说明哪一步没验。只写「该停了」的话，它读到的只有「别做了」。"""
    text = _assembled()
    assert "交出去" in text
    assert "没验" in text


def test_the_rule_keeps_the_failure_from_swallowing_the_delivery():
    """尝试不能挡住交付：卡在哪、打算怎么办，要先说出去。这一条和第一条平台规矩
    连着——房间里看不见一个闷头重试的会话，和它已经死了分不出来。

    断的是这句独有的措辞：`说出来` 在提示词里别处也有一处，拿它断的话，这条规矩
    整段删掉测试照样绿。"""
    assert "别闷头试" in _assembled()
