"""结论卡·阶段一: the wake-up prompt and the sandbox CLI subcommand.

The prompt is the only place the card's *policy* is stated to a model, so it is
worth pinning: 补证据 must not read like a rejection — it is the one path that
costs a turn, and a model that reads it as 驳回 restarts work instead of adding
the one missing piece.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.conclusion.services import need_evidence_prompt

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_cli():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


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
