"""@all / @here are reserved broadcast tokens (fusion-design §3): _resolve_mentions
treats them as RESOLVED (expanded to the roster later), never flagged as a bad
handle."""

from app.domain.agent.chat import _resolve_mentions

ROSTER = [{"handle": "alice", "name": "Alice", "role": "owner"}]


def test_all_is_resolved_not_flagged():
    resolved, unresolved = _resolve_mentions("<@all> 大家好", ROSTER)
    assert resolved == ["all"]
    assert unresolved == []


def test_here_is_resolved_not_flagged():
    resolved, unresolved = _resolve_mentions("<@here> 在的人看下", ROSTER)
    assert resolved == ["here"]
    assert unresolved == []


def test_unknown_handle_still_flagged():
    resolved, unresolved = _resolve_mentions("<@ghost> ?", ROSTER)
    assert resolved == []
    assert unresolved == ["ghost"]


def test_all_alongside_real_handle():
    resolved, unresolved = _resolve_mentions("<@all> and <@alice>", ROSTER)
    assert set(resolved) == {"all", "alice"}
    assert unresolved == []
