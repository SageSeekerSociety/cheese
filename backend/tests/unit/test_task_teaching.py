"""AI 队友辅导 (#8d772257, 10-20 空间这一轮) —— 解析、以及它出现在 prompt 里的样子。

这个文件盯住两条纪律，各占一半：

**空间和题目两层同时生效。** `teaching` 不再走「最具体的整份替换」那条链：空间
说怎么帮、题目在上面加（`Teaching.combine`），项目集那一层不再读，项目自己的设置
整份替换。其余四个键（资源包、条件、默认角色、壳）照旧是项目集 → 赛题 → 项目，
`test_every_key_rides_the_same_three_levels` 盯着它们。

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

GUIDE = {
    "mode": "guide",
    "system_prompt": "先弄清学生卡在哪一步，再决定怎么帮。",
    "material_ids": [7],
    "document_ids": ["0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e"],
}


def _index(*lines: str) -> MemoryIndex:
    """A turn's L1 index, built by hand — the prompt only ever sees this shape."""
    return MemoryIndex(
        sections=[IndexSection(label="项目", prefix="team", text="\n".join(lines))],
        warnings=[],
    )


def _space(teaching=None) -> SimpleNamespace:
    return SimpleNamespace(teaching=teaching or {})


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
    """一条新会话开场听到的项目现状（辅导学生那一段是现状，在开场快照里）。"""
    return build_session_opening(
        doc="DOC",
        memory=_index("- [记忆](memory.md) — MEMORY"),
        keeps_memory=True,
        **kw,
    ).text


#: One row per overridable key: the category value, the 赛题 override, the 项目
#: settings override, how to read the key back off a resolved Protocol, and how
#: to normalize a stored value into the same shape. `teaching` is not here: it
#: is the one key with two levels in force at once (see below).
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
        # `shell` made the trip for #1292: the row is here so "it rides the same
        # chain" is asserted rather than believed.
        "shell",
        "course-student",
        "workbench",
        "default",
        lambda p: p.shell,
        lambda stored: stored,
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
    """项目集 → 赛题 → 项目, for the four whole-key keys, most specific last."""
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


def test_the_space_and_the_task_are_both_in_force() -> None:
    """A 空间 says how its AI teammates help; a 题目 adds to it. Both
    requirements reach the agent, the 题目's way of helping wins when it picks
    one, and the materials of both are read."""
    got = resolve(
        space=_space(GUIDE),
        task=_task(
            {
                "teaching": {
                    "mode": "spec",
                    "system_prompt": "本题先写测试。",
                    "material_ids": [9, 7],
                }
            }
        ),
        project=_project(),
    )

    assert got.teaching.mode == "spec"
    assert got.teaching.space_prompt == "先弄清学生卡在哪一步，再决定怎么帮。"
    assert got.teaching.system_prompt == "本题先写测试。"
    assert got.teaching.material_ids == [7, 9]
    assert got.teaching_source == "both"


def test_a_task_giving_only_skills_keeps_the_spaces_guidance_whole() -> None:
    """Skills are not guidance: a 题目 that gives skills and says nothing else
    leaves the space's requirements, goals and private guidance in force."""
    space = {
        "system_prompt": "空间的要求",
        "learning_goals": ["写出循环"],
        "private_prompt": "参考答案",
    }
    got = resolve(
        space=_space(space),
        task=_task({"teaching": {"skills": ["wolfram"]}}),
        project=_project(),
    ).teaching

    assert got.system_prompt == "空间的要求"
    assert got.learning_goals == ["写出循环"]
    assert got.private_prompt == "参考答案"
    assert got.skills == ["wolfram"]
    section = teaching_section(TeachingContext(course=None, teaching=got)) or ""
    assert "空间的要求" in section


def test_a_blank_default_teammate_on_a_task_inherits_the_categorys() -> None:
    """A blank `default_role` (what 「继承」 stored) says nothing: the level
    above stands."""
    got = resolve(
        category=_category(default_role="fullstack-engineer"),
        task=_task({"default_role": ""}),
    )

    assert got.default_role == "fullstack-engineer"


def test_the_skills_of_the_space_and_the_task_are_both_carried() -> None:
    """Skills ride the guidance: the space's and the 题目's are both in force,
    each named once, and a level with only skills is still a configuration."""
    got = resolve(
        space=_space({"skills": ["documents"]}),
        task=_task({"teaching": {"skills": ["wolfram", "documents", "  "]}}),
        project=_project(),
    )

    assert got.teaching.skills == ["documents", "wolfram"]
    assert got.teaching_source == "both"


def test_a_task_with_only_skills_adds_no_guidance_section() -> None:
    """The skills reach the agent as skills; the 辅导学生 section has nothing to
    say for them, so it is not there at all."""
    context = TeachingContext(course=None, teaching=Teaching(skills=["wolfram"]))

    assert teaching_section(context) is None


def test_a_task_that_picks_no_way_of_helping_keeps_the_spaces() -> None:
    got = resolve(
        space=_space(GUIDE),
        task=_task({"teaching": {"system_prompt": "可以解读评测输出。"}}),
        project=_project(),
    )

    assert got.teaching.mode == "guide"


def test_one_level_alone_is_that_level() -> None:
    only_space = resolve(space=_space(GUIDE), task=_task(), project=_project())
    assert only_space.teaching == Teaching.from_json(GUIDE)
    assert only_space.teaching.space_prompt is None
    assert only_space.teaching_source == "space"

    only_task = resolve(
        space=_space(), task=_task({"teaching": GUIDE}), project=_project()
    )
    assert only_task.teaching == Teaching.from_json(GUIDE)
    assert only_task.teaching_source == "task"


def test_a_category_is_no_longer_a_level() -> None:
    """Its settings moved onto its 题目 (e5f1a9c3d7b2); what is left on it is
    not read."""
    got = resolve(
        space=_space(),
        category=_category(teaching=GUIDE),
        task=_task(),
        project=_project(),
    )

    assert got.teaching.is_empty
    assert got.teaching_source is None


def test_a_projects_own_settings_replace_both_levels() -> None:
    got = resolve(
        space=_space(GUIDE),
        task=_task({"teaching": {"system_prompt": "本题的。"}}),
        project=_project({"teaching": {"mode": "free"}}),
    )

    assert got.teaching == Teaching(mode="free")
    assert got.teaching_source == "project"


def test_the_project_level_reads_its_own_keys_and_ignores_the_rest() -> None:
    """`Project.settings` is free-form and already holds unrelated keys
    (`forge_kind`, the compute profile). Only the protocol's own names are read,
    so a stranger key cannot be mistaken for a protocol field, and a `settings`
    that is not a dict is absent rather than an error."""
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
                space=_space(GUIDE), task=_task(), project=_project(junk)
            ).teaching.mode
            == "guide"
        )


def test_no_level_configured_is_an_empty_teaching_not_an_error() -> None:
    got = resolve(category=None, task=None, project=None)

    assert got == Protocol()
    assert got.teaching == Teaching()
    assert got.teaching.is_empty is True


def test_from_json_drops_what_it_cannot_read_rather_than_raising() -> None:
    """`resolve` runs on every turn of every project: a typo in one field must
    not take down every 题目 that reads it."""
    for junk in (None, [], "guide", 7):
        assert Teaching.from_json(junk) == Teaching()

    got = Teaching.from_json(
        {
            "mode": "lecture",  # not one of the four
            "system_prompt": 42,
            "current_week": 3,  # an old key, gone
            "material_ids": [7, "8", 0, -1, True, 7],  # strings/positivity/bool/dupe
            "document_ids": ["not-a-uuid", None],
        }
    )

    assert got == Teaching(material_ids=[7])


def test_a_context_with_no_teaching_renders_nothing() -> None:
    assert (
        teaching_section(TeachingContext(course="创研课 2026 秋", teaching=Teaching()))
        is None
    )


def test_the_section_carries_the_course_the_way_of_helping_and_the_课件() -> None:
    context = TeachingContext(
        course="创研课 2026 秋",
        teaching=Teaching.from_json(GUIDE),
        materials=[{"id": 7, "name": "第 3 周讲义.pdf", "url": "/uploads/m7.pdf"}],
        documents=[
            {"id": "0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e", "title": "递归与分治"}
        ],
    )

    section = teaching_section(context)

    assert section is not None
    assert section.splitlines()[0] == "## 辅导学生（课程：创研课 2026 秋）"
    assert "以提问引导学生思考" in section  # the way of helping, in words
    assert "先弄清学生卡在哪一步，再决定怎么帮。" in section
    assert "### 课程的要求" not in section  # one level: no headings for two
    assert "第 3 周讲义.pdf" in section and "/uploads/m7.pdf" in section
    assert (
        "递归与分治" in section and "id=0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e" in section
    )


def test_both_levels_are_said_apart() -> None:
    """The agent reads which requirement is the course's and which the 题目's."""
    teaching = Teaching.from_json(GUIDE).combine(
        Teaching.from_json({"system_prompt": "本题先写测试。"})
    )

    section = teaching_section(TeachingContext(course=None, teaching=teaching))

    assert section is not None
    assert section.splitlines()[0] == "## 辅导学生"
    assert "### 课程的要求\n先弄清学生卡在哪一步，再决定怎么帮。" in section
    assert "### 这道题的要求\n本题先写测试。" in section


def test_the_prompt_of_a_non_course_project_is_byte_identical() -> None:
    """The whole point of `teaching=None` being the default.

    Asserted as text equality rather than "does not contain 辅导学生": what has
    to hold is that nothing moved — a stray blank line or a reordered section
    would be a change to every project on the platform, and this is the test
    that would notice.
    """
    baseline = _prompt()

    assert _prompt(teaching=None) == baseline
    # An empty context is the same as no context: the section is skipped, not
    # rendered empty. This is what a course project with blank guidance gets.
    assert (
        _prompt(teaching=TeachingContext(course="创研课 2026 秋", teaching=Teaching()))
        == baseline
    )


def test_a_new_session_of_a_course_project_is_told_how_to_help() -> None:
    """It shapes every answer, so a session has to hear it before it starts on
    anything."""
    prompt = _prompt(
        teaching=TeachingContext(
            course="创研课 2026 秋", teaching=Teaching.from_json({"mode": "example"})
        )
    )

    assert "## 辅导学生" in prompt


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
    """只有范围没有目标（纯课件）：不是教学任务，别把苏格拉底话术塞进
    人家的项目。"""
    section = teaching_section(
        TeachingContext(course=None, teaching=Teaching.from_json({"material_ids": [3]}))
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


def test_a_teaching_change_waits_for_the_next_session() -> None:
    """教学配置**不**是逐轮重说的现状：一个会话保持开场那一份，直到下一次新会话。

    这是 #8d772257 定的语义（`harness/prompt.py` 模块说明第 3 条），#4525 把它写进
    了 `TRACKED_SECTIONS` 的注释里。往一个上下文里全是旧范围的会话塞新范围，比让它
    按旧的做完这一轮更糟。"""
    from app.domain.agent.harness.prompt import TRACKED_SECTIONS

    assert "teaching" not in TRACKED_SECTIONS


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

    # None 是「没写」：不改动已经存下的那一格。
    apply_task_default_role(task, None)
    assert task.protocol_override["default_role"] == "academic-research"

    # 空串是「继承」：清掉这道题自己的，项目集那份重新生效。
    apply_task_default_role(task, "")
    assert "default_role" not in task.protocol_override
    assert task.protocol_override["shell"] == "course"
    assert (
        resolve(category=_category(default_role="fullstack-engineer"), task=task)
    ).default_role == "fullstack-engineer"


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
    from app.api.space_payloads import teaching_to_api

    teaching = Teaching(private_prompt="参考答案是 42")

    assert "privatePrompt" not in teaching_to_api(teaching)
    assert teaching_to_api(teaching, include_private=True)["privatePrompt"] == (
        "参考答案是 42"
    )
