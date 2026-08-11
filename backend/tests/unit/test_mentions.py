"""_expand_mention_names: friendly "@名字 / @handle / @话题名" → structured
tokens (<@handle> / <#id>) that the UI renders as clickable chips.

Regression context: people are labeled by handle in chat, so they naturally
type "@andyl" — which used to stay plain text (not clickable) whenever the
member's display name differed from the handle.
"""

from app.domain.agent.chat import _expand_mention_names, _resolve_mentions

ROSTER = [
    {"handle": "andyl", "name": "Andy Liu", "role": "owner"},
    {"handle": "zhang-heng", "name": "张衡", "role": "member"},
]


def test_expands_display_name():
    out = _expand_mention_names("@张衡 这周过一下方案", ROSTER)
    assert out == "<@zhang-heng> 这周过一下方案"


def test_expands_handle_even_when_display_name_differs():
    # The reported bug: "@andyl" (handle) must become a clickable token too.
    out = _expand_mention_names("@andyl 都办好了", ROSTER)
    assert out == "<@andyl> 都办好了"


def test_expands_multiword_ascii_name():
    out = _expand_mention_names("@Andy Liu 看一下", ROSTER)
    assert out == "<@andyl> 看一下"


def test_ascii_boundary_prevents_prefix_match():
    # Handle "andy" must not eat the front of a longer word "@andyl".
    roster = [{"handle": "andy", "name": "andy", "role": "member"}]
    assert _expand_mention_names("@andyl 你好", roster) == "@andyl 你好"


def test_longest_match_wins_between_members():
    roster = [
        {"handle": "andy", "name": "andy", "role": "member"},
        {"handle": "andyl", "name": "andyl", "role": "member"},
    ]
    assert _expand_mention_names("@andyl 你好", roster) == "<@andyl> 你好"


def test_cjk_name_matches_without_trailing_space():
    # 中文点名后面往往直接跟正文，不加空格。
    out = _expand_mention_names("@张衡来负责", ROSTER)
    assert out == "<@zhang-heng>来负责"


def test_existing_token_untouched():
    out = _expand_mention_names("<@andyl> 已经通知了", ROSTER)
    assert out == "<@andyl> 已经通知了"


def test_plain_name_without_at_is_not_touched():
    out = _expand_mention_names("张衡 说他没空", ROSTER)
    assert out == "张衡 说他没空"


def test_member_with_empty_name_does_not_swallow_every_at():
    roster = [{"handle": "ghost", "name": "", "role": "member"}]
    out = _expand_mention_names("邮件发到 a@b.com", roster)
    assert out == "邮件发到 a@b.com"


def test_expands_topic_title_to_topic_token():
    topics = [
        {"id": "abc12345-0000-0000-0000-000000000000", "title": "搭建推荐算法原型"}
    ]
    out = _expand_mention_names("进展同步到 @搭建推荐算法原型", ROSTER, topics)
    assert out == "进展同步到 <#abc12345-0000-0000-0000-000000000000>"


def test_resolve_mentions_splits_known_and_hallucinated():
    resolved, unresolved = _resolve_mentions("<@andyl> 和 <@nobody> 看下", ROSTER)
    assert resolved == ["andyl"]
    assert unresolved == ["nobody"]


def test_empty_roster_accuses_nobody():
    # 私聊 (and any caller with no member list) passes an empty roster. "I have
    # no list" must not render as "你不是项目成员" — that copy sent people hunting
    # through the project member table for someone who is plainly in it.
    resolved, unresolved = _resolve_mentions("<@andyl> 看下", [])
    assert resolved == []
    assert unresolved == []


def test_broadcast_token_needs_no_roster_entry():
    resolved, unresolved = _resolve_mentions("<@all> 都看下", ROSTER)
    assert resolved == ["all"]
    assert unresolved == []


def test_empty_roster_does_not_silence_a_broadcast():
    # Regression: @all/@here expand from the TOPIC roster (a DB read in
    # _notify_mentions), never from this list — an empty list here must not
    # swallow the broadcast. Bailing out early on `not roster` did exactly that.
    resolved, unresolved = _resolve_mentions("<@all> 都看下", [])
    assert resolved == ["all"]
    assert unresolved == []
