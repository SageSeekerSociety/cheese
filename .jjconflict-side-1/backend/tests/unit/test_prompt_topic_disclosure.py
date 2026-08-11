"""话题列表的渐进式披露：system prompt 只渲染活跃话题，`@标题` 的解析表保持全量。

最容易做错的地方是把两者合成一份——那样"少注入"会顺手砍掉引用能力：用户自己打
`@某个已归档话题` 就不再变成 <#id> 链接。这里的测试就是冲着那个失败模式来的。
"""

import uuid

from app.domain.agent.chat import (
    PLACEHOLDER_TITLE,
    _build_system_prompt,
    _expand_mention_names,
    _topic_ref_lists,
)
from app.domain.topic.models import Topic, TopicKind, TopicStatus

ARCHIVED_ID = uuid.uuid4()
CURRENT_ID = uuid.uuid4()


def _topic(
    title: str,
    *,
    status: TopicStatus = TopicStatus.active,
    kind: TopicKind = TopicKind.topic,
    topic_id: uuid.UUID | None = None,
) -> Topic:
    return Topic(
        id=topic_id or uuid.uuid4(),
        title=title,
        status=status,
        kind=kind,
    )


def _project_topics() -> list[Topic]:
    return [
        _topic("cheese 自建 · 项目总览", kind=TopicKind.root),
        _topic("当前这个话题", topic_id=CURRENT_ID),
        _topic("搭建推荐算法原型"),
        _topic("分页调研"),
        _topic(PLACEHOLDER_TITLE),
        _topic(PLACEHOLDER_TITLE),
        _topic("设备能力检查(零消耗)"),
        _topic("设备能力检查(零消耗)"),
        _topic("两阶段采纳闭环", status=TopicStatus.archived, topic_id=ARCHIVED_ID),
        _topic("JWT 时区 bug", status=TopicStatus.archived),
    ]


def _titles(refs: list[dict]) -> list[str]:
    return [r["title"] for r in refs]


def test_prompt_list_drops_archived_placeholder_and_duplicate_titles():
    _, for_prompt = _topic_ref_lists(_project_topics(), exclude_id=CURRENT_ID)

    assert _titles(for_prompt) == ["搭建推荐算法原型", "分页调研"]


def test_mention_table_stays_complete():
    """验收标准 2：解析表必须仍是全量——归档的、未命名的、同名的都还在里面。"""
    full, _ = _topic_ref_lists(_project_topics(), exclude_id=CURRENT_ID)

    titles = _titles(full)
    assert "两阶段采纳闭环" in titles  # archived
    assert "JWT 时区 bug" in titles  # archived
    assert PLACEHOLDER_TITLE in titles
    assert titles.count("设备能力检查(零消耗)") == 2
    # 自己和 root 依然被排除（原有行为不变）
    assert "当前这个话题" not in titles
    assert "cheese 自建 · 项目总览" not in titles


def test_archived_topic_mention_still_expands_to_token():
    """验收标准 2 的功能形态：一个**没被列进 prompt** 的已归档话题，
    `@标题` 仍然解析成 <#id>。"""
    full, for_prompt = _topic_ref_lists(_project_topics(), exclude_id=CURRENT_ID)
    assert "两阶段采纳闭环" not in _titles(for_prompt)  # 前提：它确实没被列出来

    out = _expand_mention_names("这个在 @两阶段采纳闭环 里讨论过", [], full)

    assert out == f"这个在 <#{ARCHIVED_ID}> 里讨论过"


def test_private_style_empty_input_yields_two_empty_lists():
    assert _topic_ref_lists([], exclude_id=CURRENT_ID) == ([], [])


def test_prompt_section_lists_only_active_and_says_how_to_find_archived():
    """验收标准 1+3：prompt 那一段只出现活跃话题，且说明文字不能让 agent 以为
    归档话题不存在/不可访问。"""
    _, for_prompt = _topic_ref_lists(_project_topics(), exclude_id=CURRENT_ID)

    prompt = _build_system_prompt("base", "", None, [], topics=for_prompt)

    assert "- 搭建推荐算法原型" in prompt
    assert "两阶段采纳闭环" not in prompt
    assert "没列出来 ≠ 不存在" in prompt
    assert 'cheese api GET "/topics?project_id=$CHEESE_PROJECT"' in prompt


def test_duplicate_titles_resolve_to_the_first_one_only():
    """本卡第二节要求核实的静默 bug：同名标题在 expand_mention_names 里只有第一个
    生效，其余静默指向那一个。这里把现状钉住——正因如此，同名标题不进 prompt。"""
    first, second = uuid.uuid4(), uuid.uuid4()
    topics = [
        {"id": str(first), "title": "设备能力检查(零消耗)"},
        {"id": str(second), "title": "设备能力检查(零消耗)"},
    ]

    out = _expand_mention_names("看 @设备能力检查(零消耗)", [], topics)

    assert out == f"看 <#{first}>"  # 第二个话题按标题根本引用不到
