"""产物清单进每一轮的开场 (#1085 结论三)。

它在系统提示里不是为了让芝士读一遍就算了：交付时点名用的就是这几个名字，而写错一
个不会报错，只会在清单上多一项看着像重复的东西。所以这里问的是「下一次交付照着它
点名，点得准吗」——名字要在，版本要在，一项都没有时这一段整个不出现。
"""

from app.domain.agent.harness.prompt import build_system_prompt


def _prompt(artifacts: list[dict] | None) -> str:
    return build_system_prompt("底稿", "", None, [], artifacts=artifacts)


REPORT = {
    "id": "8f3c1d2e-0000-4000-8000-000000000001",
    "name": "结题报告",
    "version": 3,
}
SITE = {"id": "8f3c1d2e-0000-4000-8000-000000000002", "name": "项目官网", "version": 1}


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


def test_an_artifact_nobody_has_delivered_yet_says_so_instead_of_version_zero():
    prompt = _prompt([REPORT | {"version": 0}])

    assert "还没交付过" in prompt
    assert "第 0 版" not in prompt


def test_a_project_that_has_produced_nothing_gets_no_manifest_section():
    assert "产物清单" not in _prompt([])
    assert "产物清单" not in _prompt(None)
