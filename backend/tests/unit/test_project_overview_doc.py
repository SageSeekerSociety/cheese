"""项目总览：① 从文档里取，②~⑤ 从结构化数据现拼（#1889 第 1 条）。

这一版改造的要点是「谁写哪一块」：人 / 芝士只写「项目是什么」，其余四块由平台
现拼，抄不进正文也不受正文影响。这里钉的就是这条界线——把 ① 之外的正文写进
文档，注入的总览里一个字都不该出现；反过来，结构化数据里有的，不写文档也要在。
"""

from app.domain.topic.overview import (
    ACTIVE_TOPICS_LIMIT,
    DECISIONS_LIMIT,
    project_brief,
    render_overview_auto,
    topic_conclusion,
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


def test_the_current_conclusion_block_yields_its_first_sentence():
    topic_doc = (
        "## 目标\n\n支持翻页。\n\n## 当前结论\n\n"
        "分页方案已定：用 cursor（决策见 @分页调研）。\n待办：@张衡 过一遍。\n"
    )

    assert topic_conclusion(topic_doc) == (
        "分页方案已定：用 cursor（决策见 @分页调研）。"
    )
    assert topic_conclusion("## 目标\n\n支持翻页。\n") is None


def test_what_is_happening_now_is_one_linked_line_per_topic():
    text = render_overview_auto(
        active_topics=[_topic()], decisions=[], milestones=[], closed_topics=[]
    )

    assert "## 现在在做什么" in text
    assert "<#t-1> 分页接口" in text
    assert "负责人：@张衡" in text
    assert "进行中" in text
    assert "当前结论：用 cursor，不用 offset。" in text


def test_every_automatic_block_comes_from_structured_data():
    text = render_overview_auto(
        active_topics=[_topic()],
        decisions=[{"text": "先做分页", "topic": "分页接口", "topic_id": "t-1"}],
        milestones=[{"title": "第一次内测", "due": "2026-10-01", "status": "进行中"}],
        closed_topics=[{"id": "t-2", "title": "选型", "conclusion": "用 Postgres。"}],
    )

    assert "## 最近决策" in text
    assert "先做分页" in text and "<#t-1>" in text
    assert "## 里程碑" in text and "第一次内测" in text and "2026-10-01" in text
    assert "## 已结束的话题" in text
    assert "<#t-2> 选型" in text and "结论：用 Postgres。" in text


def test_an_empty_block_is_not_rendered_at_all():
    text = render_overview_auto(
        active_topics=[_topic()], decisions=[], milestones=[], closed_topics=[]
    )

    assert "## 最近决策" not in text
    assert "## 里程碑" not in text
    assert "## 已结束的话题" not in text
    assert (
        render_overview_auto(
            active_topics=[], decisions=[], milestones=[], closed_topics=[]
        )
        == ""
    )


def test_each_block_stops_at_its_own_count():
    text = render_overview_auto(
        active_topics=[
            _topic(id=f"t-{n}", title=f"话题 {n}")
            for n in range(ACTIVE_TOPICS_LIMIT + 5)
        ],
        decisions=[
            {"text": f"决策 {n}", "topic": "分页接口", "topic_id": "t-1"}
            for n in range(DECISIONS_LIMIT + 5)
        ],
        milestones=[],
        closed_topics=[],
    )

    assert text.count("- <#t-") == ACTIVE_TOPICS_LIMIT
    assert text.count("- 决策 ") == DECISIONS_LIMIT


def test_a_topic_that_never_wrote_a_conclusion_says_so():
    # 「没写」和「没有结论」对读者是两件事；编一句「进行中」出来会被当成事实。
    text = render_overview_auto(
        active_topics=[_topic(conclusion=None)],
        decisions=[],
        milestones=[],
        closed_topics=[],
    )

    assert "当前结论：（没写）" in text
