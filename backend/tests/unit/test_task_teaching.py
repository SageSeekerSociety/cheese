"""课程级教学配置 (#8d772257) —— 继承、解析、以及它出现在 prompt 里的样子。

这个文件盯住两条纪律，各占一半：

**教育字段不新开一套继承规则。** 上半段的断言和 `test_task_protocol.py` 里那三
键是同一组（项目集 → 赛题覆盖 → 项目 settings），只是换成 `teaching`
——`test_every_key_rides_the_same_three_levels` 把四个键放在同一个参数化表里，
所以「同语义」是断言出来的，不是注释里说的。

**非课程项目一个字都不多。** 下半段盯住这条：空配置既不渲染标题，也不因为传了
一个空的 `TeachingContext` 就让 prompt 变样——`build_system_prompt` 的输出与这
个键存在之前逐字节相同。取数那一半（一次查询都不发）在
`tests/integration/test_teaching_context.py`。
"""

from types import SimpleNamespace

import pytest

from app.domain.agent.harness.prompt import build_system_prompt, teaching_section
from app.domain.task.protocol import Protocol, Teaching, resolve
from app.domain.task.teaching import TeachingContext

WEEK_THREE = {
    "system_prompt": "本周是第 {current_week} 周，重点是 {allowed_topics}。",
    "current_week": 3,
    "allowed_topics": ["循环", "数组"],
    "avoid_in_code": ["递归"],
    "material_ids": [7],
    "knowledge_ids": [11],
}


def _category(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        resource_pack=kw.get("resource_pack", {}),
        conditions=kw.get("conditions", []),
        default_role=kw.get("default_role"),
        teaching=kw.get("teaching", {}),
    )


def _task(override=None) -> SimpleNamespace:
    return SimpleNamespace(protocol_override=override)


def _project(settings=None) -> SimpleNamespace:
    return SimpleNamespace(settings={} if settings is None else settings)


def _prompt(**kw) -> str:
    return build_system_prompt(
        "BASE",
        "SKILLS",
        "DOC",
        ["MEMORY"],
        role="ROLE",
        **kw,
    )


#: One row per overridable key: the category value, the 赛题 override, the 项目
#: settings override, how to read the key back off a resolved Protocol, and how
#: to normalize a stored value into the same shape. `teaching` is the one row
#: whose two differ — it resolves to a dataclass, the others stay the JSON they
#: were stored as. That is a difference in the data, not in the rule, and this
#: table is the only place it is allowed to show.
_KEYS = [
    (
        "resource_pack",
        {"compute_credits": 500},
        {"compute_credits": 2000},
        {"compute_credits": 3000},
        lambda p: p.resource_pack,
        lambda stored: stored,
    ),
    (
        "conditions",
        [{"required_topic": "结题答辩", "reviewer_role": "mentor"}],
        [{"required_topic": "中期检查", "reviewer_role": "mentor"}],
        [{"required_topic": "开题", "reviewer_role": "mentor"}],
        lambda p: p.conditions,
        lambda stored: stored,
    ),
    (
        "default_role",
        "academic",
        "hacker",
        "mentor",
        lambda p: p.default_role,
        lambda stored: stored,
    ),
    (
        "teaching",
        WEEK_THREE,
        {"current_week": 9},
        {"current_week": 12},
        lambda p: p.teaching,
        Teaching.from_json,
    ),
]


@pytest.mark.parametrize(
    ("key", "category_value", "task_override", "project_override", "read", "parse"),
    _KEYS,
    ids=[row[0] for row in _KEYS],
)
def test_every_key_rides_the_same_three_levels(
    key, category_value, task_override, project_override, read, parse
) -> None:
    """项目集 → 赛题 → 项目, for all four keys, most specific last.

    The failure this is written against is a `teaching` field that gets its own
    resolution path ("education config is different, it needs the current week")
    — and then disagrees with the other three the first time someone edits one.
    """
    only_category = resolve(
        category=_category(**{key: category_value}), task=_task(), project=_project()
    )
    assert read(only_category) == parse(category_value)

    overridden_by_task = resolve(
        category=_category(**{key: category_value}),
        task=_task({key: task_override}),
        project=_project(),
    )
    assert read(overridden_by_task) == parse(task_override)

    overridden_by_project = resolve(
        category=_category(**{key: category_value}),
        task=_task({key: task_override}),
        project=_project({"protocol": {key: project_override}}),
    )
    assert read(overridden_by_project) == parse(project_override)


def test_a_level_replaces_its_key_whole_and_leaves_the_others() -> None:
    """Whole-key replacement, not a deep merge — the 项目集 placement's reason,
    applied to `teaching` because it is the same rule and not a second one.

    A half-inherited week (第 3 周 from the 项目集, 范围 from the 赛题) reads as a
    coherent configuration and is not one.
    """
    got = resolve(
        category=_category(
            teaching=WEEK_THREE, resource_pack={"compute_credits": 500}
        ),
        task=_task({"teaching": {"current_week": 9}}),
        project=_project(),
    )

    assert got.teaching.current_week == 9
    assert got.teaching.allowed_topics == []  # replaced, not merged
    assert got.teaching.system_prompt is None
    assert got.compute_credits == 500  # an untouched key is still inherited


def test_the_project_level_reads_the_protocol_key_and_ignores_the_rest() -> None:
    """`Project.settings` is free-form and already holds unrelated keys
    (`forge_kind`, the compute profile). Only `settings["protocol"]` is read, and
    anything else under it that is not a dict is treated as absent — the reader
    here runs on every turn of every project, not on the form that wrote it."""
    assert resolve(
        category=_category(),
        task=_task(),
        project=_project({"forge_kind": "forgejo"}),
    ) == Protocol()

    for junk in ({"protocol": "not-a-dict"}, {"protocol": ["nope"]}, {"protocol": 7}):
        assert resolve(
            category=_category(teaching=WEEK_THREE), task=_task(), project=_project(junk)
        ).teaching.current_week == 3


def test_no_level_configured_is_an_empty_teaching_not_an_error() -> None:
    got = resolve(category=None, task=None, project=None)

    assert got == Protocol()
    assert got.teaching == Teaching()
    assert got.teaching.is_empty is True


def test_from_json_drops_what_it_cannot_read_rather_than_raising() -> None:
    """`resolve` runs on every turn of every project: a typo in one field of a
    项目集 must not take down the twenty 赛题 under it."""
    for junk in (None, [], "week 3", 7):
        assert Teaching.from_json(junk) == Teaching()

    got = Teaching.from_json(
        {
            "system_prompt": 42,
            "current_week": "第三周",
            "allowed_topics": "循环",  # a bare string, not a list
            "avoid_in_code": ["递归", "", "  ", 7, "递归"],  # blanks + dupe + non-text
            "material_ids": [7, "8", 0, -1, True, 7],  # strings/positivity/bool/dupe
            "knowledge_ids": None,
        }
    )

    assert got.system_prompt is None
    assert got.current_week is None
    assert got.allowed_topics == []
    assert got.avoid_in_code == ["递归"]
    assert got.material_ids == [7]
    assert got.knowledge_ids == []


def test_true_is_not_week_one() -> None:
    """`True` is an int in Python, so an unguarded read would answer 第 1 周 to a
    field a teacher left as a checkbox — same footgun as `compute_credits`."""
    assert Teaching.from_json({"current_week": True}).current_week is None


def test_fill_substitutes_the_week_variables() -> None:
    template = Teaching.from_json(WEEK_THREE)

    assert template.fill(template.system_prompt or "") == (
        "本周是第 3 周，重点是 循环、数组。"
    )


def test_fill_leaves_braces_it_does_not_know_alone() -> None:
    """A 讲义 that quotes a dict literal at the agent must not be read as format
    fields: `str.format` would raise on `{"key": ...}` and take down every turn
    of the course, naming a brace rather than the config that caused it."""
    template = Teaching.from_json({"system_prompt": '示例：{"key": 1} 与 {未知}'})

    assert template.fill(template.system_prompt or "") == '示例：{"key": 1} 与 {未知}'


def test_a_context_with_no_teaching_renders_nothing() -> None:
    assert (
        teaching_section(TeachingContext(course="创研课 2026 秋", teaching=Teaching()))
        is None
    )


def test_the_section_carries_the_course_the_week_scope_and_the_课件() -> None:
    context = TeachingContext(
        course="创研课 2026 秋",
        teaching=Teaching.from_json(WEEK_THREE),
        materials=[{"id": 7, "name": "第 3 周讲义.pdf", "url": "/uploads/m7.pdf"}],
        knowledge=[{"id": 11, "name": "递归与分治", "description": "上节课的补充"}],
    )

    section = teaching_section(context)

    assert section is not None
    assert "创研课 2026 秋" in section
    assert "第 3 周" in section
    assert "本周是第 3 周，重点是 循环、数组。" in section  # the template, filled
    assert "- 循环" in section and "- 数组" in section
    assert "递归" in section  # avoid_in_code
    assert "第 3 周讲义.pdf" in section and "/uploads/m7.pdf" in section
    assert "递归与分治" in section and "id=11" in section


def test_a_course_that_never_named_itself_still_reads() -> None:
    """A 项目集 can carry a 教学安排 without a name. That has to produce a
    heading, not an empty pair of brackets."""
    section = teaching_section(
        TeachingContext(course=None, teaching=Teaching.from_json({"current_week": 4}))
    )

    assert section is not None
    assert section.splitlines()[0] == "## 本周教学范围（第 4 周）"


def test_the_prompt_of_a_non_course_project_is_byte_identical() -> None:
    """The whole point of `teaching=None` being the default.

    Asserted as text equality rather than "does not contain 教学范围": what has
    to hold is that nothing moved — a stray blank line or a reordered section
    would be a change to every project on the platform, and this is the test
    that would notice.
    """
    baseline = _prompt()

    assert _prompt(teaching=None) == baseline
    # An empty context is the same as no context: the section is skipped, not
    # rendered empty. This is what a course project with a blank 教学安排 gets.
    assert (
        _prompt(teaching=TeachingContext(course="创研课 2026 秋", teaching=Teaching()))
        == baseline
    )


def test_the_teaching_section_sits_after_the_role_and_before_the_skills() -> None:
    """It constrains what this week is about, so it has to be readable before
    the skills explain how to do it — and it is not a persona, so it does not
    belong above the role."""
    prompt = _prompt(
        teaching=TeachingContext(
            course="创研课 2026 秋", teaching=Teaching.from_json({"current_week": 3})
        )
    )

    assert prompt.index("## 你的专家角色") < prompt.index("## 本周教学范围")
    assert prompt.index("## 本周教学范围") < prompt.index("SKILLS")
