"""产物清单进每一轮的开场 (#1085 结论三)。

它在系统提示里不是为了让芝士读一遍就算了：交付时点名用的就是这几个名字，而写错一
个不会报错，只会在清单上多一项看着像重复的东西。所以这里问的是「下一次交付照着它
点名，点得准吗」——名字要在，版本要在。

清单空着的时候这一段以**短指针**出现：它问的是另一个问题——一个交文件的项目，第一
次交付只能新建，而那一下起的名字会留在清单上。短指针只留三件不能少的（怎么新建、
合并不用声明、细则去 accept-request --help 看）；整套说明跟着清单走，不跟着每一轮
走（#1535 之前空清单也全量注入，指令:信息约 8:1）。指针不在的话，提示里一个字都没
提产物，芝士只剩递卡被打回这一条路能知道要声明——而递卡是一整轮工作的最后一步。

而它还要说清**哪一种交付根本不用声明**：交出去一次合并的，交的是项目那个仓库，平
台自己认得出是哪一项。这一句不在的话，一个代码项目的每一条分支都会在这里读到「给
它起个名字」，清单于是长成一份改动列表。
"""

from app.domain.agent.harness.prompt import build_system_prompt


def _prompt(artifacts: list[dict] | None) -> str:
    return build_system_prompt("底稿", "", None, [], artifacts=artifacts)


REPORT = {
    "id": "8f3c1d2e-0000-4000-8000-000000000001",
    "name": "结题报告",
    "version": 3,
    "about": "交给甲方的最终报告",
}
SITE = {
    "id": "8f3c1d2e-0000-4000-8000-000000000002",
    "name": "项目官网",
    "version": 1,
    "about": "对外的产品介绍站",
}


def test_the_names_a_delivery_has_to_choose_from_are_in_the_prompt():
    prompt = _prompt([REPORT, SITE])

    assert "《结题报告》" in prompt
    assert "《项目官网》" in prompt
    assert "第 3 版" in prompt
    # 沿用和新建是两个动作，清单这一段要把它们分开说 —— 只说「点名一项」的话，
    # 写错的名字会被当成新建，而那是错得最安静的一种。
    assert "artifact" in prompt and "new_artifact" in prompt
    # 沿用只认 id，所以清单上每一项的 id 必须在这里 —— 不在的话，点名它的唯一办法
    # 就是写名字，而那正是要挡住的那一下。
    assert REPORT["id"] in prompt and SITE["id"] in prompt
    # 光有名字判断不了「我做出来的是不是它的新一版」——《结题报告》和手上那份
    # 「期中分析.pdf」，谁也说不准。那一句话得跟着名字一起在场。
    assert REPORT["about"] in prompt and SITE["about"] in prompt


def test_an_artifact_nobody_has_delivered_yet_says_so_instead_of_version_zero():
    prompt = _prompt([REPORT | {"version": 0}])

    assert "还没交付过" in prompt
    assert "第 0 版" not in prompt


def test_a_project_that_has_produced_nothing_is_told_to_name_the_first_one():
    prompt = _prompt([])

    assert "产物清单" in prompt
    # 空清单上没有 id 可抄，所以交文件、交地址的那一次只能新建；把沿用也摆出来只
    # 会让它去猜一个 id。两个参数连着断言：`about` 这个词在「合并不用声明」那句里
    # 也出现，单独断言它，空清单这一支把它整个掉了也照样绿。
    assert "`new_artifact=<真名>` 加 `about=<一句话>`" in prompt
    # 整套检验跟着清单走，不跟着空清单走（#1535）：空清单这一段是短指针。
    assert "第 1 版和第 20 版都成立" not in prompt


def test_the_prompt_says_a_merge_declares_nothing():
    """一个代码项目的每一条分支都会读到这一段。它要是只说「给它起个名字」，清单就
    会按分支长出一行行来 —— 那正是这一句要挡住的。"""
    for artifacts in ([], [REPORT]):
        prompt = _prompt(artifacts)

        assert "不用声明产物" in prompt
        assert "仓库" in prompt


def test_the_sentence_and_the_name_come_with_how_to_check_them():
    """规则会忘，检验方法当场能自查 —— 所以两者一起给。"""
    prompt = _prompt([REPORT])

    assert "第 1 版和第 20 版都成立" in prompt


def test_a_room_that_does_not_deliver_gets_no_manifest_section():
    assert "产物清单" not in _prompt(None)
