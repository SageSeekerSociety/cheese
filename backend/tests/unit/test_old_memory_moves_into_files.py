"""迁移的规矩：每条旧记忆都有去处，private 不上 project，先报告后写入。

纯模块（`app/domain/memory/migration.py`）的那一半：旧表长什么样、模型说了什么、
新树会写成什么样、报告上写着什么。写下去以后会发生什么（版本冲突、事务）在
`tests/integration/test_the_old_table_moves_only_after_a_human_says_yes.py` 里。
"""

import pytest

from app.domain.memory.files import MemoryFileScope
from app.domain.memory.migration import (
    MigrationError,
    OldEntry,
    Source,
    build_plan,
    decode_answer,
    migration_prompt,
    overview_entries,
    sources_digest,
)

_DOC = """## 项目是什么

一套给高中生做的算法课。

## 大家都该知道的

- 发版前跑一遍 `make e2e`，它是唯一会打真数据库的那条。

部署走 GitHub Actions，手动触发。

## 项目记忆（由记忆整理迁入）

分页接口用 cursor，不用 offset。

## 现在在做什么

这一块不该被搬。
"""


def _entry(
    source_id: str,
    content: str,
    *,
    origin: Source = Source.agent,
    where: str = "池：芝士-A 在这个项目学到的",
) -> OldEntry:
    return OldEntry(source_id=source_id, origin=origin, where=where, content=content)


def _project_file(name: str, version: int = 3) -> tuple[MemoryFileScope, str, str, int]:
    return (MemoryFileScope.project, "", f"{name}.md", version)


def _index_scope() -> tuple[MemoryFileScope, str, str, int]:
    return (MemoryFileScope.project, "", "MEMORY.md", 7)


def _decision(source: str, destination: str, **fields) -> dict:
    return {
        "source": source,
        "destination": destination,
        "reason": "因为它在这儿",
        **fields,
    }


# --- 来源：总览文档 --------------------------------------------------------


def test_only_the_two_named_sections_are_read():
    entries = overview_entries(_DOC)

    assert [entry.source_id for entry in entries] == [
        "overview:大家都该知道的#1",
        "overview:大家都该知道的#2",
        "overview:项目记忆（由记忆整理迁入）#1",
    ]
    # 按空行切段：一段是一条，而 `## 现在在做什么` 不是来源。
    assert all("现在在做什么" not in entry.content for entry in entries)
    assert all(entry.origin is Source.overview for entry in entries)


def test_a_document_without_them_yields_nothing():
    assert overview_entries("## 目标\n\n做一件事。\n") == []


def test_the_digest_moves_when_one_line_moves():
    before = overview_entries(_DOC)
    after = overview_entries(_DOC.replace("cursor", "keyset"))

    assert sources_digest(before) == sources_digest(overview_entries(_DOC))
    assert sources_digest(before) != sources_digest(after)


# --- 模型那段 JSON --------------------------------------------------------


def test_the_answer_can_be_an_object_or_a_bare_list():
    one = [{"source": "a", "destination": "discard", "reason": "r"}]

    assert decode_answer('{"decisions": ' + str(one).replace("'", '"') + "}") == one
    assert decode_answer(str(one).replace("'", '"')) == one


def test_a_fenced_answer_is_still_read():
    fenced = '```json\n{"decisions": []}\n```'

    assert decode_answer(fenced) == []


@pytest.mark.parametrize(
    "answer",
    ["不是 JSON", '{"decisions": "nope"}', "[1, 2]", ""],
)
def test_an_unreadable_answer_is_refused(answer):
    with pytest.raises(MigrationError):
        decode_answer(answer)


# --- 计划：去哪儿、写什么 --------------------------------------------------


def _plan(entries, raw, *, existing=None, owners=None, files=None):
    return build_plan(
        project_id="p-1",
        entries=entries,
        existing=existing if existing is not None else {},
        owners=owners if owners is not None else ["alice"],
        files=files if files is not None else [_index_scope()],
        raw_decisions=raw,
    )


def test_a_new_project_memory_and_a_new_private_one_are_both_planned():
    entries = [
        _entry("e1", "分页用 cursor"),
        _entry("e2", "他偏好小 PR", origin=Source.person),
    ]
    plan = _plan(
        entries,
        [
            _decision(
                "e1",
                "project",
                path="pagination-uses-cursor",
                type="project",
                description="分页接口用 cursor",
                body="分页接口用 cursor。\n\n**Why:** offset 在深页上慢。",
            ),
            _decision(
                "e2",
                "private",
                owner="alice",
                path="prefers-small-prs",
                type="feedback",
                description="他偏好小 PR",
                body="这一带的改动，他偏好一个合起来的 PR。",
            ),
        ],
    )

    paths = sorted(planned.prefixed for planned in plan.files)
    assert paths == [
        "private/alice/prefers-small-prs.md",
        "project/pagination-uses-cursor.md",
    ]
    project = next(f for f in plan.files if f.prefixed.startswith("project/"))
    assert project.is_new and project.version is None
    assert project.content.startswith(
        "---\nname: pagination-uses-cursor\ndescription: 分页接口用 cursor\n"
        "type: project\n---\n"
    )
    # 索引跟着涨：两个新文件两条指针，而且是整份新内容（旧行留着）。
    index = next(item for item in plan.indexes if item.scope is MemoryFileScope.project)
    assert index.version == 7
    assert index.added_lines == 1  # 私人的那一条进的是 alice 自己的索引
    assert "旧行" not in index.content
    assert "索引" in plan.report() and "project 的索引" in plan.report()
    assert "project/pagination-uses-cursor.md" in plan.report()


def test_an_entry_without_a_destination_stops_the_whole_plan():
    entries = [_entry("e1", "一"), _entry("e2", "二")]

    with pytest.raises(MigrationError) as refused:
        _plan(entries, [_decision("e1", "discard")])

    assert "e2" in str(refused.value)


def test_one_entry_cannot_have_two_destinations():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "一")],
            [_decision("e1", "discard"), _decision("e1", "discard")],
        )

    assert "两个去处" in str(refused.value)


def test_a_decision_about_an_unknown_source_is_refused():
    with pytest.raises(MigrationError):
        _plan([_entry("e1", "一")], [_decision("e9", "discard")])


def test_every_destination_needs_a_reason():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "一")],
            [{"source": "e1", "destination": "discard", "reason": "  "}],
        )

    assert "理由" in str(refused.value)


# --- 两条硬规矩 ------------------------------------------------------------


def test_what_is_about_a_person_never_becomes_a_project_rule():
    """「不许把 private 的内容升级进 project」在代码里长这样。"""
    entry = _entry("e1", "他不喜欢末尾总结", origin=Source.person)

    with pytest.raises(MigrationError) as refused:
        _plan(
            [entry],
            [
                _decision(
                    "e1",
                    "project",
                    path="no-closing-summary",
                    type="feedback",
                    description="不要末尾总结",
                    body="回答末尾不要总结。",
                )
            ],
        )

    assert "不能进 project" in str(refused.value)


def test_the_same_entry_can_go_to_that_persons_private_tree():
    plan = _plan(
        [_entry("e1", "他不喜欢末尾总结", origin=Source.person)],
        [
            _decision(
                "e1",
                "private",
                owner="alice",
                path="no-closing-summary",
                type="feedback",
                description="不要末尾总结",
                body="回答末尾不要总结。",
            )
        ],
    )

    assert plan.files[0].prefixed == "private/alice/no-closing-summary.md"


def test_a_user_type_memory_cannot_land_in_project():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "他写了十年 Go")],
            [
                _decision(
                    "e1",
                    "project",
                    path="alice-go",
                    type="user",
                    description="他写 Go",
                    body="他写了十年 Go。",
                )
            ],
        )

    assert "user 永远 private" in str(refused.value)


def test_a_private_memory_must_have_a_known_owner():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "他喜欢短回答")],
            [
                _decision(
                    "e1",
                    "private",
                    owner="bob",
                    path="short-answers",
                    type="feedback",
                    description="短回答",
                    body="他要短回答。",
                )
            ],
            owners=["alice"],
        )

    assert "bob" in str(refused.value)


# --- 合并：往一条已经存在的记忆上补 ----------------------------------------


_SHELF = (
    "---\nname: integration-tests-hit-a-real-db\ndescription: 集成测试打真库\n"
    "type: feedback\n---\n\n集成测试必须打真数据库，不许 mock。\n"
)


def test_a_merge_appends_to_the_body_and_leaves_the_frontmatter_alone():
    plan = _plan(
        [_entry("e1", "上个季度吃过 mock 的亏")],
        [
            _decision(
                "e1",
                "merge",
                path="integration-tests-hit-a-real-db",
                body="**Why:** 上个季度 mock 的测试全过，生产的迁移炸了。",
            )
        ],
        existing={
            "project/integration-tests-hit-a-real-db.md": _SHELF,
            "project/MEMORY.md": (
                "- [integration-tests-hit-a-real-db]"
                "(integration-tests-hit-a-real-db.md) — 打真库\n"
            ),
        },
        files=[_project_file("integration-tests-hit-a-real-db"), _index_scope()],
    )

    merged = plan.files[0]
    assert merged.is_new is False
    assert merged.version == 3
    assert merged.content.startswith(_SHELF.rstrip())
    assert "**Why:** 上个季度" in merged.content
    assert merged.content.count("name: integration-tests-hit-a-real-db") == 1
    # 合并的那一条已经有索引行了，不再加一行。
    assert plan.indexes == ()


def test_merging_into_something_that_is_not_there_is_refused():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "一")],
            [_decision("e1", "merge", path="nope", body="正文")],
        )

    assert "merge" in str(refused.value)


def test_a_new_file_may_not_land_on_top_of_an_existing_one():
    """新建撞上已经存在的文件，写下去就是整份覆盖——这次迁移没有这个动作。"""
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "一")],
            [
                _decision(
                    "e1",
                    "project",
                    path="integration-tests-hit-a-real-db",
                    type="feedback",
                    description="打真库",
                    body="正文",
                )
            ],
            existing={"project/integration-tests-hit-a-real-db.md": _SHELF},
            files=[_project_file("integration-tests-hit-a-real-db"), _index_scope()],
        )

    assert "已经存在" in str(refused.value)


def test_nothing_may_be_written_into_the_index_itself():
    with pytest.raises(MigrationError) as refused:
        _plan(
            [_entry("e1", "一")],
            [
                _decision(
                    "e1",
                    "project",
                    path="MEMORY",
                    type="project",
                    description="正文",
                    body="正文",
                )
            ],
        )

    assert "索引" in str(refused.value)


# --- 索引：加行、不重复加 --------------------------------------------------


def test_a_line_that_is_already_in_the_index_is_not_added_twice():
    index = {
        "project/MEMORY.md": "- [no-closing-summary](no-closing-summary.md) — 别总结\n",
        "project/no-closing-summary.md": _SHELF,
    }
    plan = _plan(
        [_entry("e1", "一")],
        [
            _decision(
                "e1",
                "merge",
                path="no-closing-summary",
                body="补一句。",
            )
        ],
        existing=index,
        files=[_project_file("no-closing-summary"), _index_scope()],
    )

    assert plan.indexes == ()


def test_a_new_memory_gets_one_line_in_the_index_of_its_own_tree():
    plan = _plan(
        [
            _entry("e1", "一"),
            _entry("e2", "二", origin=Source.person),
            _entry("e3", "三", origin=Source.person),
        ],
        [
            _decision(
                "e1",
                "project",
                path="pagination-uses-cursor",
                type="project",
                description="分页用 cursor",
                body="正文。",
            ),
            _decision(
                "e2",
                "private",
                owner="alice",
                path="short-answers",
                type="feedback",
                description="短回答",
                body="正文。",
            ),
            _decision(
                "e3",
                "private",
                owner="alice",
                path="prefers-small-prs",
                type="feedback",
                description="小 PR",
                body="正文。",
            ),
        ],
    )

    # 两棵树各有一份索引：project 一份，alice 一份。私人的那两条不进 project 的索引。
    assert len(plan.indexes) == 2
    project = next(
        index for index in plan.indexes if index.scope is MemoryFileScope.project
    )
    assert project.content.count("- [") == 1
    alice = next(index for index in plan.indexes if index.owner == "alice")
    assert alice.added_lines == 2
    assert alice.content.count("- [") == 2
    # 索引没有标题行：一行一条指针，行数就是条数。
    assert plan.project_index_lines() == 1


def test_the_report_says_whether_the_project_index_made_the_goal():
    before = "- [a](a.md) — 一行\n" * 119
    plan = _plan(
        [_entry("e1", "一")],
        [
            _decision(
                "e1",
                "project",
                path="b",
                type="project",
                description="b",
                body="正文。",
            )
        ],
        existing={"project/MEMORY.md": before},
        files=[_index_scope()],
    )

    assert plan.project_index_lines() == 120
    assert plan.over_goal() is False
    assert "达标" in plan.report()


def test_the_report_says_it_loudly_when_the_index_is_still_too_long():
    before = "- [a](a.md) — 一行\n" * 130
    plan = _plan(
        [_entry("e1", "一")],
        [
            _decision(
                "e1",
                "project",
                path="b",
                type="project",
                description="b",
                body="正文。",
            )
        ],
        existing={"project/MEMORY.md": before},
        files=[_index_scope()],
    )

    assert plan.over_goal() is True
    assert "没达标" in plan.report()


# --- 建议与丢弃 ------------------------------------------------------------


def test_a_suggestion_is_listed_and_writes_nothing():
    plan = _plan(
        [_entry("e1", "测试目录叫 tests/unit")],
        [
            _decision(
                "e1",
                "suggest",
                target="CLAUDE.md",
                body="仓库约定那一节可以写明测试怎么分目录。",
            )
        ],
    )

    assert plan.files == () and plan.indexes == ()
    assert [item.target for item in plan.suggestions] == ["CLAUDE.md"]
    report = plan.report()
    assert "只是建议，不自动改" in report
    assert "CLAUDE.md" in report
    # 一个字都不动 CLAUDE.md：计划里没有它。
    assert all("CLAUDE.md" not in planned.prefixed for planned in plan.files)


def test_a_suggestion_without_a_target_is_refused():
    with pytest.raises(MigrationError):
        _plan([_entry("e1", "一")], [_decision("e1", "suggest", body="写进哪儿呢")])


def test_a_discard_is_reported_with_its_reason():
    plan = _plan(
        [_entry("e1", "README 里写着怎么跑测试")],
        [_decision("e1", "discard", reason="读一眼 README 就知道，不值得记")],
    )

    assert plan.files == ()
    assert "读一眼 README 就知道" in plan.report()
    assert "旧表一个字都不删" in plan.report()


def test_the_counts_add_up():
    plan = _plan(
        [_entry("e1", "一"), _entry("e2", "二")],
        [
            _decision("e1", "discard"),
            {
                "source": "e2",
                "destination": "suggest",
                "target": "SKILL.md",
                "reason": "r",
            },
        ],
    )

    assert plan.counts()["discard"] == 1
    assert plan.counts()["suggest"] == 1
    assert plan.counts()["project"] == 0


# --- 问模型的那一段 --------------------------------------------------------


def test_the_prompt_carries_the_sources_the_people_and_the_existing_tree():
    prompt = migration_prompt(
        project_id="p-1",
        project_name="cheese 自建",
        entries=[_entry("e1", "分页用 cursor")],
        owners=["alice"],
        existing={
            "project/MEMORY.md": "- [a](a.md) — 钩子一句\n",
            "project/a.md": "正文",
        },
    )

    assert "cheese 自建" in prompt and "p-1" in prompt
    assert "alice" in prompt
    assert "`project/a.md` — 钩子一句" in prompt
    assert "`e1`" in prompt and "分页用 cursor" in prompt
