"""点名解析那一块搬出来之后，自己站得住。

与 ``test_event_lines.py`` / ``test_prompt_module.py`` 同一套口径，三条：

- 直接 import 新模块就能测，不经过 ``ChatService``（这个文件不 import chat 的实例
  状态，也不起任何 app fixture）；
- 搬走的名字在 ``app.domain.agent.chat`` 上仍然导得出来——那是兼容门面，既有调用点
  与测试不用改一行；
- 新模块不反向 import chat，否则门面就成了循环。

行为本身（friendly ``@名字`` 怎么展开、私聊里名册为空、群播 token 怎么放行、通知发
给谁、编辑只补发新增的那几个）由 ``test_mentions.py``、``test_broadcast_mentions.py``、
``test_message_edit.py``、``test_chat_publication.py`` 与
``test_a_private_chats_two_seats_live_in_the_roster.py`` 从门面那条路径覆盖。这里补的
是搬出来之后新出现的两样东西：模块边界，和原先没有直接单测的那几件——``<#id>``
引用 token 的解析、``person_mentions`` 那个 ``dm`` 参数（`_is_dm` 仍钉在
chat.py，那个布尔不该在第二个模块里出现）。
"""

import ast
import inspect
import pathlib

from app.domain.agent import chat, mentions
from app.domain.agent.mentions import (
    _SPECIAL_MENTIONS,
    MENTION_ALL,
    MENTION_HERE,
    _resolve_mentions,
    _topic_refs,
)

#: 搬走的全部名字。门面要逐个还得出同一个对象。
MOVED = (
    "MENTION_ALL",
    "MENTION_HERE",
    "PersonMentions",
    "_MENTION_RE",
    "_SPECIAL_MENTIONS",
    "_TOPIC_REF_RE",
    "_expand_mention_names",
    "_resolve_mentions",
    "_topic_refs",
    "announce_mentions",
    "person_mentions",
    "project_refs_text",
)

ROSTER = [{"handle": "alice", "name": "Alice", "role": "owner"}]


# ---- 门面：搬走的名字还是同一个对象，导入路径没变 ----


def test_the_moved_names_are_the_same_objects_behind_the_facade():
    for name in MOVED:
        assert getattr(chat, name) is getattr(mentions, name), name


def test_the_module_does_not_import_the_facade_back():
    """反向 import 就是循环，门面也就不是门面了。"""
    tree = ast.parse(pathlib.Path(mentions.__file__).read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {
        alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    }
    assert "app.domain.agent.chat" not in modules


def test_the_definitions_are_not_left_behind_in_chat():
    """门面是「重新导出」，不是「两份定义」——两份定义会各自漂移。"""
    tree = ast.parse(pathlib.Path(chat.__file__).read_text())
    defined = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not (defined & set(MOVED)), sorted(defined & set(MOVED))


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

    `is_private` 全仓只有 chat.py 的 `_is_dm` 一个读点，而且那道棘轮连形参与关键
    字实参都数（`test_is_private_read_points.py`）——参数按名字递进来，那个布尔就
    不会在这里多出一个出处。"""
    signature = inspect.signature(mentions.person_mentions)
    dm = signature.parameters["dm"]
    assert dm.kind is inspect.Parameter.KEYWORD_ONLY
    assert dm.default is inspect.Parameter.empty
