"""点名解析由真实 intake 调用，不依赖 ChatService 兼容门面。

直接测点名模块的 token、解析与参数契约。

完整发送、通知与编辑行为由 ``test_mentions.py``、``test_broadcast_mentions.py``、
``test_message_edit.py``、``test_chat_publication.py`` 与
``test_a_private_chats_two_seats_live_in_the_roster.py`` 覆盖。这里补
``<#id>`` 引用解析与 ``person_mentions`` 的 ``dm`` 参数；唯一 `_is_dm` 读点
属于 `turn/intake/rooms.py`，不能在点名模块里再问一遍。
"""

import inspect

from app.domain.agent import mentions
from app.domain.agent.mentions import (
    _SPECIAL_MENTIONS,
    MENTION_ALL,
    MENTION_HERE,
    _resolve_mentions,
    _topic_refs,
)

ROSTER = [{"handle": "alice", "name": "Alice", "role": "owner"}]


# ---- `<#topicId>` 引用 token：原先没有直接单测 ----


def test_one_topic_token_becomes_one_topic_ref():
    assert _topic_refs("看 <#0f9c1e2d-aaaa-bbbb-cccc-ddddeeeeffff> 那条") == [
        "topic:0f9c1e2d-aaaa-bbbb-cccc-ddddeeeeffff"
    ]


def test_a_repeated_topic_token_is_one_ref():
    text = "<#abcdef01> 和 <#abcdef01> 是同一间房"
    assert _topic_refs(text) == ["topic:abcdef01"]


def test_topic_refs_keep_the_order_they_appear_in():
    assert _topic_refs("<#bbbbbbbb> 先，<#aaaaaaaa> 后") == [
        "topic:bbbbbbbb",
        "topic:aaaaaaaa",
    ]


def test_text_without_topic_tokens_has_no_refs():
    assert _topic_refs("没有引用") == []
    assert _topic_refs("") == []
    assert _topic_refs(None) == []


def test_a_too_short_token_is_not_a_topic_ref():
    """`<#1234>` 那种短的（半个 uuid、或者一个话题标题里的井号）不算引用。"""
    assert _topic_refs("<#1234>") == []


# ---- 保留群播 token：形状与解析 ----


def test_special_mentions_are_exactly_all_and_here():
    assert MENTION_ALL == "all"
    assert MENTION_HERE == "here"
    assert _SPECIAL_MENTIONS == {"all", "here"}


def test_resolve_mentions_answers_both_lists():
    resolved, unresolved = _resolve_mentions("<@alice> 和 <@nobody> 看下", ROSTER)
    assert resolved == ["alice"]
    assert unresolved == ["nobody"]


def test_an_empty_roster_confirms_and_refutes_nothing():
    """私聊／没有成员行的项目：具体 handle 两个列表都不进，群播照常放行。"""
    resolved, unresolved = _resolve_mentions("<@nobody> 和 <@all>", [])
    assert resolved == ["all"]
    assert unresolved == []


def test_the_same_handle_twice_resolves_once():
    resolved, _ = _resolve_mentions("<@alice> 又 <@alice>", ROSTER)
    assert resolved == ["alice"]


# ---- 那一问由调用方递进来，模块里不再问第二遍 ----


def test_person_mentions_takes_the_private_room_answer_as_a_keyword():
    """``dm`` 必填且 keyword-only：这一块不去读 `is_private`。

    轮次组装只在 `turn/intake/rooms.py` 的 `_is_dm` 问一次「是不是私聊」，而
    `is_private` 这个名字只许出现在 `topic/models.py`，连形参与关键字实参都算
    （`test_is_private_read_points.py`）——参数按名字递进来，那个布尔就不会在这里
    多出一个出处。"""
    signature = inspect.signature(mentions.person_mentions)
    dm = signature.parameters["dm"]
    assert dm.kind is inspect.Parameter.KEYWORD_ONLY
    assert dm.default is inspect.Parameter.empty
