"""课程级教学配置 (#8d772257) —— 继承、解析、以及它出现在 prompt 里的样子。

这个文件盯住两条纪律，各占一半：

**教育字段不新开一套继承规则。** 上半段的断言和 `test_task_protocol.py` 里那三
键是同一组（项目集 → 赛题覆盖 → 项目 settings），只是换成 `teaching`
——`test_every_key_rides_the_same_three_levels` 把四个键放在同一个参数化表里，
所以「同语义」是断言出来的，不是注释里说的（`shell` 那一行同理，它随 #1292 进来）。

**非课程项目一个字都不多。** 下半段盯住这条：空配置既不渲染标题，也不因为传了
一个空的 `TeachingContext` 就让开场快照变样——`build_session_opening` 的输出与这
个键存在之前逐字节相同。取数那一半（一次查询都不发）在
`tests/integration/test_teaching_context.py`。
"""

from types import SimpleNamespace

import pytest

from app.domain.agent.harness.prompt import build_session_opening, teaching_section
from app.domain.memory.files_store import IndexSection, MemoryIndex
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


def _index(*lines: str) -> MemoryIndex:
    """A turn's L1 index, built by hand — the prompt only ever sees this shape."""
    return MemoryIndex(
        sections=[IndexSection(label="项目", prefix="team", text="\n".join(lines))],
        warnings=[],
    )


def _category(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        resource_pack=kw.get("resource_pack", {}),
        conditions=kw.get("conditions", []),
        default_role=kw.get("default_role"),
        shell=kw.get("shell"),
        teaching=kw.get("teaching", {}),
    )


def _task(override=None) -> SimpleNamespace:
    return SimpleNamespace(protocol_override=override)


def _project(settings=None) -> SimpleNamespace:
    return SimpleNamespace(settings={} if settings is None else settings)


def _prompt(**kw) -> str:
    """一条新会话开场听到的项目现状（教学范围是现状，在开场快照里）。"""
    return build_session_opening(
        doc="DOC",
        memory=_index("- [记忆](memory.md) — MEMORY"),
        keeps_memory=True,
        **kw,
    ).text


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
        # `shell` made the trip for #1292 and `teaching` for this task: the row
        # is here so "they ride the same chain" is asserted for both rather than
        # believed, and so the union of the two keys survives the next edit.
        "shell",
        "course-student",
        "workbench",
        "default",
        lambda p: p.shell,
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

    The failure this is written against is a key that gets its own
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
        project=_project({key: project_override}),
    )
    assert read(overridden_by_project) == parse(project_override)


def test_a_level_replaces_its_key_whole_and_leaves_the_others() -> None:
    """Whole-key replacement, not a deep merge — the 项目集 placement's reason,
    applied to `teaching` because it is the same rule and not a second one.

    A half-inherited week (第 3 周 from the 项目集, 范围 from the 赛题) reads as a
    coherent configuration and is not one.
    """
    got = resolve(
        category=_category(teaching=WEEK_THREE, resource_pack={"compute_credits": 500}),
        task=_task({"teaching": {"current_week": 9}}),
        project=_project(),
    )

    assert got.teaching.current_week == 9
    assert got.teaching.allowed_topics == []  # replaced, not merged
    assert got.teaching.system_prompt is None
    assert got.compute_credits == 500  # an untouched key is still inherited


def test_the_project_level_reads_its_own_keys_and_ignores_the_rest() -> None:
    """`Project.settings` is free-form and already holds unrelated keys
    (`forge_kind`, the compute profile). Only the protocol's own names are read,
    spelled FLAT — the same place `app.domain.shell.service.SHELL_KEY` reads a
    project's 壳 — so a stranger key cannot be mistaken for a protocol field, and
    a `settings` that is not a dict is absent rather than an error. The reader
    here runs on every turn of every project, not on the form that wrote it."""
    assert (
        resolve(
            category=_category(),
            task=_task(),
            project=_project({"forge_kind": "forgejo"}),
        )
        == Protocol()
    )

    for junk in ("not-a-dict", ["nope"], 7):
        assert (
            resolve(
                category=_category(teaching=WEEK_THREE),
                task=_task(),
                project=_project(junk),
            ).teaching.current_week
            == 3
        )


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


def test_a_new_session_of_a_course_project_is_told_this_weeks_scope() -> None:
    """It constrains what this week is about, so a session has to hear it before
    it starts on anything."""
    prompt = _prompt(
        teaching=TeachingContext(
            course="创研课 2026 秋", teaching=Teaching.from_json({"current_week": 3})
        )
    )

    assert "## 本周教学范围" in prompt


# ── 教育性 (#944)：学习目标 = 这份指导按教学走 ────────────────────────────────


def test_goals_alone_get_the_default_teaching_manners() -> None:
    """写了学习目标而老师没写自己的要求：默认教学要求顶上 —— 「留空」在这里
    是「用你的教学规矩」，不是「没规矩」。目标单列成靶子。"""
    section = teaching_section(
        TeachingContext(
            course="创研课 2026 秋",
            teaching=Teaching.from_json({"learning_goals": ["会读栈追踪"]}),
        )
    )

    assert section is not None
    assert "不要直接给出答案" in section
    assert "别把答案交出去" in section  # 冲突裁决：要得再急也不给（model_spec tutor）
    assert "亲手写" in section  # 人机分工：关键几行留给学生（learning output style）
    assert "cheese show" in section  # 形态纪律：能演示就别写长文
    assert "### 本节学习目标" in section and "- 会读栈追踪" in section


def test_the_teacher_own_prompt_wins_and_the_default_never_leaks_in() -> None:
    section = teaching_section(
        TeachingContext(
            course=None,
            teaching=Teaching.from_json(
                {"system_prompt": "这次直接讲。", "learning_goals": ["会读栈追踪"]}
            ),
        )
    )

    assert section is not None
    assert "这次直接讲。" in section
    assert "不要直接给出答案" not in section
    # 目标照列 —— 靶子不因为老师换了话术就消失。
    assert "- 会读栈追踪" in section


def test_scope_without_goals_is_not_educational() -> None:
    """只有范围没有目标（纯课件/周次）：不是教学任务，别把苏格拉底话术塞进
    人家的项目。"""
    section = teaching_section(
        TeachingContext(course=None, teaching=Teaching.from_json({"current_week": 3}))
    )

    assert section is not None
    assert "不要直接给出答案" not in section


def test_fill_substitutes_the_goals_variable() -> None:
    teaching = Teaching.from_json(
        {
            "system_prompt": "这一节要：{learning_goals}",
            "learning_goals": ["读栈追踪", "二分定位"],
        }
    )

    assert teaching.fill(teaching.system_prompt or "") == "这一节要：读栈追踪、二分定位"


def test_a_teaching_change_is_re_told_to_a_running_session() -> None:
    """教学安排是被跟踪的现状：老师推周次/改指导，正在跑的会话下一轮就听到，
    不是按上一周的指导跑到结束。"""
    from app.domain.agent.harness.prompt import TRACKED_SECTIONS

    assert "teaching" in TRACKED_SECTIONS


# ── 题目级默认队友 (#944)：同一份 `protocol_override` 里的另一格 ────────────────
# 这几条只碰「写/读一格」的胶水；`_task` 这个替身已经有 `protocol_override`。


def test_default_role_reads_back_blank_as_nothing() -> None:
    """`default_role` 的读：空串不是角色，读回来是 None（= 继承上层）。"""
    from app.api.task_teaching import read_task_default_role

    assert read_task_default_role(_task({"default_role": "  "})) is None
    assert read_task_default_role(_task({"default_role": "academic-research"})) == (
        "academic-research"
    )
    assert read_task_default_role(_task(None)) is None


def test_applying_the_default_role_leaves_the_other_keys_alone() -> None:
    """整键替换：只动 `default_role` 这一格，`teaching` 与别的覆盖键原样留着。"""
    from app.api.task_teaching import apply_task_default_role

    task = _task({"teaching": {"current_week": 3}, "shell": "course"})

    apply_task_default_role(task, "academic-research")
    assert task.protocol_override["default_role"] == "academic-research"
    assert task.protocol_override["teaching"] == {"current_week": 3}
    assert task.protocol_override["shell"] == "course"

    # 空串是一次写（明说「不要默认队友」），不是「没写」。
    apply_task_default_role(task, "")
    assert task.protocol_override["default_role"] == ""

    # None 才是「没写」：不改动已经存下的那一格。
    apply_task_default_role(task, None)
    assert task.protocol_override["default_role"] == ""


# ── 出题人私有指导 (#944 第二种可见性) ─────────────────────────────────────────
# 「用 prompt 实现可见性」：私有那段进 agent 的 prompt，但**不进学生可读的响应**
# （`teaching_to_api` 默认不带它）。不新造权限开关 —— 提不提由接口那一层决定。


def test_private_guidance_round_trips_and_counts_toward_emptiness() -> None:
    teaching = Teaching.from_json(
        {"system_prompt": "陪着他做", "private_prompt": "参考答案是 42"}
    )

    assert teaching.private_prompt == "参考答案是 42"
    # 空配置一个字都不多：但写了私有指导，这份配置就不是空的。
    assert Teaching.from_json({}).is_empty is True
    assert Teaching.from_json({"private_prompt": "参考答案"}).is_empty is False


def test_private_guidance_reaches_the_agent_prompt_under_its_own_heading() -> None:
    """私有指导是给 AI 队友的：照它做、但明确不许原样透露给学生。"""
    section = teaching_section(
        TeachingContext(course=None, teaching=Teaching(private_prompt="参考答案是 42"))
    )
    assert section is not None
    assert "出题人给你的私有指导" in section
    assert "参考答案是 42" in section


def test_private_guidance_is_omitted_from_the_response_unless_asked() -> None:
    """`teaching_to_api` 默认不带 `privatePrompt` —— 学生那条读就看不到它；
    出题人/管理员那条读传 `include_private=True` 才带。"""
    from app.api.routes.spaces import teaching_to_api

    teaching = Teaching(private_prompt="参考答案是 42")

    assert "privatePrompt" not in teaching_to_api(teaching)
    assert teaching_to_api(teaching, include_private=True)["privatePrompt"] == (
        "参考答案是 42"
    )
