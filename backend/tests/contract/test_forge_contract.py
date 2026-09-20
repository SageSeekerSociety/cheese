"""The contract between the acceptance flow and a forge.

One side real (the whole accept path: guards, votes, viewed revision, the card),
the other side a double (`tests/support/ledger_forge.py`, whose conclusion the
test dictates). What is pinned here is that the platform READS the forge's
conclusion and acts on it, rather than deciding for it — and that it does so
through capability bits, not through "is this project on GitHub".

结论 50：用户不为了用我们而改任何东西。结论 51：评审发生在用户所在的地方。
不变量 I21c、I23、I26。#363。
"""

import pathlib
import uuid as _uuid

import pytest

from app.domain.review import forge as forge_mod
from app.domain.review import merge_state
from tests.integration.test_accept_pr import (
    _accept,
    _cards,
    _make_card,
    _make_project,
    _make_topic,
    _poll,
)
from tests.support.ledger_forge import code_project_forge, doc_project_forge


@pytest.fixture
def ledger(monkeypatch):
    """Put a forge of the test's choosing behind the accept path."""

    def _install(forge):
        async def resolve(**_):
            return forge

        monkeypatch.setattr(forge_mod, "resolve", resolve)
        return forge

    return _install


def _attach_proposal(client, card_id: str, url: str) -> None:
    import asyncio

    from app.domain.review.repositories import AcceptCardRepository

    async def _do():
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(_uuid.UUID(card_id))
            card.pr_number = 42
            card.pr_url = url
            await session.commit()

    asyncio.run(_do())


def _bare_card(client) -> tuple[str, str]:
    """一个「用户什么都没配」的项目上的一张卡：没有远端、没有装任何东西。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    return tid, _make_card(client, tid)


# ---- 三个动作，各一条 --------------------------------------------------------


def test_merging_goes_through_the_forge(client, ledger):
    forge = ledger(doc_project_forge(conclusion="green"))
    tid, cid = _bare_card(client)

    accepted = _accept(client, cid)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"
    assert [c[0] for c in forge.calls] == ["accept"]


def test_a_proposal_with_no_displayed_revision_goes_back_to_the_forge(client, ledger):
    """提案这个动作：卡上有提案页而浏览器没声明看到哪一版，托管方去刷新它。"""
    forge = ledger(code_project_forge(conclusion="green"))
    tid, cid = _bare_card(client)
    _attach_proposal(client, cid, "https://forge.example/proposals/42")

    refused = _accept(client, cid, head_sha=None)

    assert refused.status_code == 422, refused.text
    assert "还没在页面上显示过" in refused.text
    assert [c[0] for c in forge.calls] == ["refresh"]


def test_reading_the_conclusion_goes_through_the_forge(client, ledger):
    """读检查结论这个动作：平台自己不算，它把托管方说的镜像到卡上。"""
    forge = ledger(code_project_forge(conclusion="running"))
    tid, cid = _bare_card(client)
    _attach_proposal(client, cid, "https://forge.example/proposals/42")

    assert _poll(client)["cards_checked"] == 1

    assert [c[0] for c in forge.calls] == ["poll"]
    card = _cards(client, tid)[0]
    assert card["merge_state"]["state"] == "blocked"
    assert "CI 还在跑" in card["merge_state"]["reasons"][0]["detail"]


# ---- 结论是什么，采纳就按什么走 ----------------------------------------------


@pytest.mark.parametrize(
    ("conclusion", "needle"),
    [("red", "必跑检查红了"), ("running", "CI 还在跑")],
)
def test_a_conclusion_that_is_not_green_stops_the_accept(
    client, ledger, conclusion, needle
):
    """红拦住，**还在跑也拦住** —— 没有结论不是通过（#465/#468）。"""
    ledger(doc_project_forge(conclusion=conclusion))
    tid, cid = _bare_card(client)

    refused = _accept(client, cid)

    assert refused.status_code == 422, refused.text
    assert needle in refused.text
    assert _cards(client, tid)[0]["status"] == "pending"


# ---- 托管方身份在卡生成的那一刻就在卡上（I23）--------------------------------


def test_the_card_carries_the_forge_before_anybody_clicks(client, ledger):
    forge = ledger(doc_project_forge())
    tid, _cid = _bare_card(client)

    card = _cards(client, tid)[0]

    assert card["forge"]["kind"] == forge.kind.value
    assert card["forge"]["identity"] == forge.capabilities.identity.value
    assert card["forge"]["reports_checks"] is False
    assert card["forge"]["declaration"] == forge.declaration
    # 而且是在人点之前：这张卡还没被采纳过。
    assert card["status"] == "pending"


def test_no_after_the_fact_forge_note_survives_anywhere():
    """I23：卡片渲染路径上「托管方是谁」只从 `forge` 字段取。

    `PLATFORM_FORGE_NOTE` was that fact's second home — written onto the card's
    note AFTER the accept, so before the click the card said nothing, and after
    it the same fact existed twice.
    """
    here = pathlib.Path(__file__).resolve()
    root = here.parents[2]
    hits = [
        str(path.relative_to(root))
        for path in (*root.glob("app/**/*.py"), *root.glob("tests/**/*.py"))
        # This file names it to say it must not exist; every other mention is one.
        if path != here and "PLATFORM_FORGE_NOTE" in path.read_text(encoding="utf-8")
    ]

    assert hits == []


# ---- 用户开了分支保护就读它的结论，没开才按平台侧的规则判（结论 50）----------


def test_the_forges_own_verdict_is_read_when_the_user_enforces_one():
    """用户在 GitHub 上开了分支保护 —— 那是他自己的规则，平台原样读，不重算。"""
    verdict = merge_state.compute_merge_state(
        github_mergeable_state="blocked",
        github_mergeable=True,
        github_enforces=True,
        # 平台侧一条必跑检查都没配 —— 若平台自己判，这里会是 clean。
        required_checks=(),
    )

    assert verdict.state == "blocked"


def test_the_platform_judges_by_the_project_rules_when_the_forge_enforces_none():
    """用户什么都没开：没有结论可读，按平台自己项目设置里的规则判。

    这一条和上一条合起来就是结论 50 —— 我们既不要求用户去 GitHub 开分支保护，
    也不在他开了的时候另算一套。
    """
    verdict = merge_state.compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        github_enforces=False,
        required_checks=(merge_state.RequiredCheck(name="test"),),
        check_runs=(
            merge_state.CheckRun(name="test", status="completed", conclusion="failure"),
        ),
    )

    assert verdict.state == "blocked"
    assert any(r.kind == "required_check_failed" for r in verdict.reasons)


# ---- 评审发生在用户所在的地方（结论 51）--------------------------------------


def test_a_forge_that_hosts_proposals_puts_a_link_on_the_card(client, ledger):
    """用户在 forge 那边：卡上是一条到提案页的链接，评审在那里进行。"""
    ledger(code_project_forge())
    tid, cid = _bare_card(client)
    _attach_proposal(client, cid, "https://forge.example/proposals/42")

    card = _cards(client, tid)[0]

    assert card["forge"]["hosts_proposals"] is True
    assert card["pr_url"] == "https://forge.example/proposals/42"


def test_a_forge_that_hosts_none_keeps_the_review_on_the_card(client, ledger):
    """同一个假 forge，能力位一改，用户就不在 forge 那边了：没有链接，评审在房间
    和卡片里，而卡片自己说明了这一点。判据是项目里实际存在什么，不是项目属于哪
    一类（#1085）。"""
    ledger(doc_project_forge())
    tid, _cid = _bare_card(client)

    card = _cards(client, tid)[0]

    assert card["forge"]["hosts_proposals"] is False
    assert card["pr_url"] is None
    assert card["forge"]["declaration"]


# ---- I21c：用户什么都没配的仓库上，三个动作完整可用 --------------------------


def test_nothing_configured_gets_a_forgejo_repository(client):
    """New projects receive a hosted repository without repository changes.

    Native proposal creation, review, and merging are exercised against the
    actual Forgejo server in tests/forgejo/test_live.py.
    """
    tid, cid = _bare_card(client)
    card = _cards(client, tid)[0]
    assert card["forge"]["kind"] == forge_mod.ForgeKind.forgejo.value
    assert card["forge"]["reports_checks"] is True
    assert card["forge"]["hosts_proposals"] is True
    assert card["merge_state"]["state"] == "unknown"
