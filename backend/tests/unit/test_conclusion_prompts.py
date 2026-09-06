"""结论卡·阶段一: the two wake-up prompts and the sandbox CLI subcommand.

The prompts are the only place the card's *policy* is stated to a model, so
they're worth pinning: the digest prompt must say 默认采信 out loud (a model that
thinks it has to act would spend a turn on every conclusion), and the
need-evidence prompt must not read like a rejection.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent.chat import conclusion_digest_prompt
from app.domain.conclusion.services import need_evidence_prompt

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_cli():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --- 消化提示词 -------------------------------------------------------------


def test_digest_prompt_without_a_card_is_the_old_prompt():
    """归档的父话题拿不到卡，提示词就得回到原来的样子——不能提一张不存在的卡。"""
    conclusion = "结论正文"
    prompt = conclusion_digest_prompt(conclusion)
    assert conclusion in prompt
    assert "结论卡" not in prompt
    assert "默认采信" not in prompt


def test_digest_prompt_leads_with_doing_nothing_being_correct():
    prompt = conclusion_digest_prompt("子话题的结论正文", card_id="card-123")
    assert "子话题的结论正文" in prompt, "结论原文必须原样带上"
    assert "card-123" in prompt, "不给卡号，父话题就没法结算"
    assert "默认采信" in prompt
    assert "不需要做任何事" in prompt
    # 两条出口都得写清楚，否则真要打回/升级时它不知道怎么做。
    assert "cheese conclusion need-evidence card-123" in prompt
    assert "cheese conclusion escalate card-123" in prompt


def test_digest_prompt_mentions_the_deadline_when_there_is_one():
    from datetime import UTC, datetime

    prompt = conclusion_digest_prompt(
        "结论", card_id="c-1", deadline=datetime(2026, 8, 11, 14, 30, tzinfo=UTC)
    )
    assert "14:30" in prompt


# --- 打回提示词 -------------------------------------------------------------


def _returned_card(reason: str = "把 p99 延迟的实测数跑出来") -> SimpleNamespace:
    return SimpleNamespace(id="card-9", task_id="task-7", settle_reason=reason)


def test_need_evidence_prompt_is_not_a_rejection():
    prompt = need_evidence_prompt(_returned_card(), task_title="分页调研")
    assert "把 p99 延迟的实测数跑出来" in prompt, "要补什么必须原样传下去"
    assert "不是驳回" in prompt
    assert "驳回" not in prompt.replace("不是驳回", ""), "别的地方不许出现驳回"


def test_need_evidence_prompt_tells_the_room_to_relay_it():
    """收这条提示词的是**房间**，不是那条活——活是房间会话里的一个分身，它没有自己
    的会话可以叫醒。所以这段话必须够房间去转达：哪条活、哪张卡、补什么、补完怎么走。"""
    prompt = need_evidence_prompt(_returned_card(), task_title="分页调研")
    assert "分页调研" in prompt, "不说是哪条活，房间不知道该找哪个分身"
    assert "card-9" in prompt, "不给卡号，补回来的结论接不上这张卡"
    assert "转达" in prompt
    assert "cheese conclude-task task-7" in prompt, "没告诉房间补完怎么交回来"


def test_need_evidence_prompt_survives_a_thread_with_no_title():
    """标题是空的也得能说人话——这段话是提示词，缺一栏不能变成一句半截的指令。"""
    prompt = need_evidence_prompt(_returned_card())
    assert "「」" not in prompt
    assert "card-9" in prompt


# --- sandbox CLI ------------------------------------------------------------


@pytest.mark.parametrize("action", ["accept", "need-evidence", "escalate"])
def test_cli_posts_to_the_receiver_topic(monkeypatch, capsys, action):
    """路由挂在收方（父话题）下，CLI 必须用 CHEESE_TOPIC 而不是卡里的子话题 id——
    per-turn token 就是按 URL 里的 topic 校验作用域的。"""
    cli = _load_cli()
    monkeypatch.setattr(cli, "TOPIC", "parent-1")
    monkeypatch.setattr(cli, "AUTHOR", "user-1")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_call",
        lambda m, p, b=None: seen.update(method=m, path=p, body=b) or {"data": {}},
    )
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "conclusion", action, "card-9", "理由"]
    )
    cli.main()

    assert seen["method"] == "POST"
    assert seen["path"] == f"/topics/parent-1/conclusion-cards/card-9/{action}"
    assert seen["body"] == {"decided_by": "user-1", "reason": "理由"}
    assert capsys.readouterr().out.strip()


def test_cli_accept_needs_no_reason(monkeypatch):
    """采信是零成本的那一边——连理由都不用写。"""
    cli = _load_cli()
    monkeypatch.setattr(cli, "TOPIC", "parent-1")
    monkeypatch.setattr(cli, "AUTHOR", "user-1")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_call",
        lambda m, p, b=None: seen.update(path=p, body=b) or {"data": {}},
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "conclusion", "accept", "card-9"])
    cli.main()
    assert seen["body"] == {"decided_by": "user-1"}


@pytest.mark.parametrize("action", ["need-evidence", "escalate"])
def test_cli_refuses_a_blank_reason_locally(monkeypatch, action):
    """本地就拦下来，省一个 422 来回。"""
    cli = _load_cli()
    monkeypatch.setattr(cli, "TOPIC", "parent-1")
    monkeypatch.setattr(cli, "AUTHOR", "user-1")
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "_call", _boom)
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "conclusion", action, "card-9", "  "]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert not called, "空理由不该发出请求"
