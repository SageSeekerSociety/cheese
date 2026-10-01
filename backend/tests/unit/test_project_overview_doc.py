"""项目总览：① 从文档里取，②~④ 从结构化数据现拼（#1889 第 1 条）。

这一版改造的要点是「谁写哪一块」：人 / 芝士只写「项目是什么」，其余三块由平台
现拼，抄不进正文也不受正文影响。这里钉的就是这条界线——把 ① 之外的正文写进
文档，注入的总览里一个字都不该出现；反过来，结构化数据里有的，不写文档也要在。
"""

from app.domain.topic.overview import (
    ACTIVE_TOPICS_KEY,
    ACTIVE_TOPICS_LIMIT,
    ACTIVE_TOPICS_TITLE,
    CLOSED_TOPICS_KEY,
    CLOSED_TOPICS_TITLE,
    MILESTONES_KEY,
    MILESTONES_LIMIT,
    MILESTONES_TITLE,
    overview_auto_blocks,
    project_brief,
    render_overview_auto,
    topic_status,
)

DOC = """## 项目是什么

给高中生做一套算法课的平台。做：课程、作业、评测；不做：直播。

## 现在在做什么

- 这一块不该有人手写。

## 大家都该知道的

- 老项目的旧小节。
"""


def _topic(**overrides) -> dict:
    row = {
        "id": "t-1",
        "title": "分页接口",
        "owner": "@张衡",
        "status": "进行中",
        "conclusion": "用 cursor，不用 offset。",
    }
    row.update(overrides)
    return row


def test_only_the_project_brief_reaches_the_prompt():
    brief = project_brief(DOC)

    assert brief.startswith("给高中生做一套算法课的平台")
    # 手写进别的块的正文一概不注入：写在那里等于没写。
    assert "这一块不该有人手写" not in brief
    assert "老项目的旧小节" not in brief


def test_a_document_without_the_brief_heading_still_arrives():
    # 一份还没按新结构写过的总览不能整个消失——静默丢掉项目共识更糟。
    assert project_brief("## 目标\n\n做一件事。\n") == "## 目标\n\n做一件事。"


def test_the_status_block_yields_its_first_sentence():
    topic_doc = (
        "## 目标\n\n支持翻页。\n\n## 现状\n\n"
        "分页方案已定：用 cursor（决策见 @分页调研）。\n待办：@张衡 过一遍。\n"
    )

    assert topic_status(topic_doc) == ("分页方案已定：用 cursor（决策见 @分页调研）。")
    assert topic_status("## 目标\n\n支持翻页。\n") is None


def test_a_doc_still_on_the_old_template_gives_its_conclusion():
    old_doc = "## 目标\n\n支持翻页。\n\n## 当前结论\n\n用 cursor。\n"

    assert topic_status(old_doc) == "用 cursor。"


def test_the_status_block_wins_over_an_old_conclusion_block():
    both = "## 当前结论\n\n旧的说法。\n\n## 现状\n\n等张衡审阅。\n"

    assert topic_status(both) == "等张衡审阅。"


def test_what_is_happening_now_is_one_linked_line_per_topic():
    text = render_overview_auto(
        active_topics=[_topic()], milestones=[], closed_topics=[]
    )

    assert "## 现在在做什么" in text
    assert "<#t-1> 分页接口" in text
    assert "负责人：@张衡" in text
    assert "进行中" in text
    assert "现状：用 cursor，不用 offset。" in text


def test_every_automatic_block_comes_from_structured_data():
    text = render_overview_auto(
        active_topics=[_topic()],
        milestones=[{"title": "第一次内测", "due": "2026-10-01", "status": "进行中"}],
        closed_topics=[{"id": "t-2", "title": "选型", "conclusion": "用 Postgres。"}],
    )

    assert "## 里程碑" in text and "第一次内测" in text and "2026-10-01" in text
    assert "## 已结束的话题" in text
    assert "<#t-2> 选型" in text and "结论：用 Postgres。" in text


def test_an_empty_block_is_not_rendered_at_all():
    text = render_overview_auto(
        active_topics=[_topic()], milestones=[], closed_topics=[]
    )

    assert "## 里程碑" not in text
    assert "## 已结束的话题" not in text
    assert render_overview_auto(active_topics=[], milestones=[], closed_topics=[]) == ""


def test_each_block_stops_at_its_own_count():
    text = render_overview_auto(
        active_topics=[
            _topic(id=f"t-{n}", title=f"话题 {n}")
            for n in range(ACTIVE_TOPICS_LIMIT + 5)
        ],
        milestones=[
            {"title": f"里程碑 {n}", "due": None, "status": "upcoming"}
            for n in range(MILESTONES_LIMIT + 5)
        ],
        closed_topics=[],
    )

    assert text.count("- <#t-") == ACTIVE_TOPICS_LIMIT
    assert text.count("- 里程碑 ") == MILESTONES_LIMIT


def test_a_topic_that_never_wrote_a_conclusion_says_so():
    # 「没写」和「没有结论」对读者是两件事；编一句「进行中」出来会被当成事实。
    text = render_overview_auto(
        active_topics=[_topic(conclusion=None)],
        milestones=[],
        closed_topics=[],
    )

    assert "现状：（没写）" in text


def test_the_structured_blocks_carry_where_each_line_leads():
    """结构化那一份给的是点得动的条目，不是排好版的字。

    界面要拿它跳转（话题房间、里程碑），所以每条都带着自己的去处；同一
    批数据在 markdown 那一份里排成 `<#id>` 的样子。两边读的是同一个 `_*_items()`，
    所以字段永远不会一头有一头没有。
    """
    blocks = overview_auto_blocks(
        active_topics=[_topic()],
        milestones=[
            {"id": "m-1", "title": "第一次内测", "due": "2026-10-01", "status": "done"}
        ],
        closed_topics=[{"id": "t-2", "title": "选型", "conclusion": "用 Postgres。"}],
    )

    assert [b["key"] for b in blocks] == [
        ACTIVE_TOPICS_KEY,
        MILESTONES_KEY,
        CLOSED_TOPICS_KEY,
    ]
    assert [b["title"] for b in blocks] == [
        ACTIVE_TOPICS_TITLE,
        MILESTONES_TITLE,
        CLOSED_TOPICS_TITLE,
    ]
    active, milestone, closed = (b["items"][0] for b in blocks)
    assert active == {
        "kind": "topic",
        "topic_id": "t-1",
        "title": "分页接口",
        "owner": "@张衡",
        "status": "进行中",
        "conclusion": "用 cursor，不用 offset。",
    }
    assert milestone["milestone_id"] == "m-1" and milestone["status"] == "done"
    assert milestone["due"] == "2026-10-01"
    assert closed["topic_id"] == "t-2" and closed["conclusion"] == "用 Postgres。"


def test_an_empty_block_is_left_out_of_the_structured_blocks_too():
    """空块整块不出现——一份「里程碑（暂无）」对读者也是噪音。"""
    blocks = overview_auto_blocks(
        active_topics=[_topic()], milestones=[], closed_topics=[]
    )

    assert [b["key"] for b in blocks] == [ACTIVE_TOPICS_KEY]
    assert overview_auto_blocks(active_topics=[], milestones=[], closed_topics=[]) == []


def test_the_structured_blocks_stop_at_the_same_counts_as_the_markdown():
    blocks = overview_auto_blocks(
        active_topics=[
            _topic(id=f"t-{n}", title=f"话题 {n}")
            for n in range(ACTIVE_TOPICS_LIMIT + 5)
        ],
        milestones=[],
        closed_topics=[],
    )

    assert len(blocks[0]["items"]) == ACTIVE_TOPICS_LIMIT
