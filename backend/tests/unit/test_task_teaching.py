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
