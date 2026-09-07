"""PR 上发生的事，一件都不许丢在路上。

一个开着的 PR 可以同时踩到好几件要人动手的事。这里钉的是它们之间的**关系**：

- 三件事同时发生，三条都送到（谁都不许把别人挡掉）；
- 同一批事实重复轮询只说一次，事实变了就再说一次（去重按内容，不按「叫过了」）；
- 从 GitHub 来的文本进芝士上下文之前，控制字符已经不在了。

GitHub 全程是 test double（`FakeGitHubPrClient`），复用 `test_accept_app_waits_
for_ci` 的那套世界 —— 它模拟的是真正在生产上跑的那条路：平台 GitHub App 开 PR，
scheduler 轮询推进。
"""

import asyncio
import uuid

import pytest

from app.domain.review import github_pr
from app.domain.review.notes import NoteCode
from app.domain.review.pr_signals import REVIEW_NUDGE_LIMIT, ReviewSignal
from tests.conftest import wait_work_idle
from tests.integration.test_accept_pr import (
    _cards,
    _poll,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)


def _authorized(client, app_world) -> tuple[str, str, int, str]:
    """一张等采纳、骑着 PR 的卡（#718：不再有「授权」，事件在 pending 上回流）。"""
    _pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    return tid, cid, number, head_sha

#: 复用「接了平台 GitHub App、卡停在 pr_open 等 CI」那个世界 —— 生产上这条链路
#: 就跑在它上面。重新包一次而不是直接 import 那个 fixture：直接 import 会让它在
#: 本模块里既是导入名又是 fixture 名，读起来像一次意外的覆盖。
app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def _nudges(client, topic_id: str, event_type: str) -> list[dict]:
    """房间里这一类平台提示的全部消息。

    读的是 `meta.event_type` 而不是文案：文案随时会改，类别码是契约
    （`app/domain/agent/platform_notices.py`）。
    """
    return [
        b
        for b in _blocks(client, topic_id)
        if (b.get("meta") or {}).get("event_type") == event_type
    ]


def _all_text(client, topic_id: str) -> str:
    """房间里所有消息的正文 + 折叠区原文，拼成一坨好做「不许出现」类断言。"""
    return "\n".join(
        f"{b.get('content') or ''}\n{(b.get('meta') or {}).get('detail') or ''}"
        for b in _blocks(client, topic_id)
    )


def _review(
    rid: int, *, kind: str = "changes_requested", body: str = "这里要改"
) -> ReviewSignal:
    return ReviewSignal(id=f"review:{rid}", kind=kind, author="bob", body=body)


def _comment(cid: int, *, body: str = "这行有问题", where: str = "app/x.py:3"):
    return ReviewSignal(
        id=f"comment:{cid}", kind="comment", author="bob", body=body, where=where
    )


def _card_row(client, card_id: str) -> dict:
    """卡的原始行，含不在对外 schema 里的 `nudge_state`。"""
    from app.domain.review.repositories import AcceptCardRepository

    out: dict = {}

    async def _do() -> None:
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            out["nudge_state"] = dict(card.nudge_state or {})
            out["note"] = card.note
            out["note_code"] = card.note_code

    asyncio.run(_do())
    return out


def _set_note(client, card_id: str, *, code: NoteCode | None, text: str) -> None:
    """把卡面改成某个状态 —— 模拟「别的东西刚在这张卡上写过一句」。"""
    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> None:
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.note = text
            card.note_code = code
            await session.commit()

    asyncio.run(_do())


# ---- 验收标准 1：三件事同时发生，一件都不许被吞掉 ----------------------------


def test_a_red_ci_never_swallows_the_review_and_the_conflict(client, app_world):
    """这是整条活的核心。

    以前的写法是「CI 红了 → nudge → return」，那个 return 站得太早：同一个 PR 上
    有人要求改动、而且它已经和主分支冲突了，只要 CI 同时是红的，芝士就一个字都
    收不到。三件事互不蕴含，所以三条都必须到。
    """
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    fake.reviews_by_number[number] = [_review(1)]
    fake.mergeable_by_number[number] = False

    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "ci_failed")) == 1
    assert len(_nudges(client, tid, "pr_review")) == 1
    assert len(_nudges(client, tid, "pr_conflict")) == 1


def test_the_card_shows_the_loudest_one_but_still_sends_them_all(client, app_world):
    """卡面是一行，三件事都要人动手 —— 卡上留优先级最高的那句，消息一条不少。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    fake.reviews_by_number[number] = [_review(1)]

    _poll(client)
    wait_work_idle()

    card = _cards(client, tid)[0]
    assert "Backend Test" in card["note"]  # CI 压过评审意见
    assert len(_nudges(client, tid, "pr_review")) == 1  # 但评审意见照样送到了


# ---- 验收标准 2：去重按内容 --------------------------------------------------


def test_the_same_failure_is_only_announced_once(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    for _ in range(3):
        _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "ci_failed")) == 1


def test_a_changed_failure_on_the_same_commit_is_announced_again(client, app_world):
    """同一个 commit 上又挂了一个 job —— 那是芝士没见过的新事实。

    旧的去重键是「卡上已经写着 checks_failed」，它对失败**内容**一无所知，所以
    这一条永远发不出去：芝士只知道「有东西红了」，不知道后来又红了一项。
    """
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _poll(client)
    wait_work_idle()

    fake.check_state_by_sha[head_sha] = (
        "failure",
        "Backend Test: failure、Lint: failure",
    )
    _poll(client)
    wait_work_idle()

    ci = _nudges(client, tid, "ci_failed")
    assert len(ci) == 2
    assert "Lint" in _all_text(client, tid)


def test_another_note_landing_on_the_card_does_not_re_announce(client, app_world):
    """去重键不再是卡面。

    真实路径：token 抖了一下，卡上写了「轮询暂停」；下一轮 token 好了，平台把那句
    清掉（`advance_pr_card` 里的自愈）。卡面回到空，而 CI 那片红一个字没变 ——
    旧写法在这里会把同一批失败重新叫一遍。
    """
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    _poll(client)
    wait_work_idle()
    assert len(_nudges(client, tid, "ci_failed")) == 1

    _set_note(client, cid, code=NoteCode.poll_paused, text="轮询暂停，下一轮还会重试")
    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "ci_failed")) == 1


def test_the_dedup_key_lives_on_the_card_not_in_memory(client, app_world):
    """签名必须落库 —— 存进程里的话，后端重启一次就是所有在飞的 PR 各被重叫一遍。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    _poll(client)
    wait_work_idle()

    seen = _card_row(client, cid)["nudge_state"].get("seen") or {}
    assert "ci" in seen and seen["ci"]


# ---- 验收标准 4：评审意见回流，带上限 ----------------------------------------


def test_review_comments_reach_the_agent_with_their_text(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    fake.reviews_by_number[number] = [_comment(9, body="这里越界了")]

    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "pr_review")) == 1
    text = _all_text(client, tid)
    assert "这里越界了" in text
    assert "app/x.py:3" in text


def test_inline_comments_are_not_fetched_when_the_pr_says_it_has_none(
    client, app_world
):
    """省下的那次请求：PR 自己报了行内评论数是 0，就别再去列一遍。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")

    _poll(client)

    assert fake.review_signal_calls == [(number, False)]


def test_review_nudges_stop_after_a_few_rounds_and_ask_for_a_human(client, app_world):
    """CI 和冲突修好就消失，评审意见不会 —— 来回几轮还不收敛就该叫人，
    而不是安静地继续催下去。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")

    for i in range(REVIEW_NUDGE_LIMIT + 1):
        fake.reviews_by_number[number] = [_review(n) for n in range(i + 1)]
        _poll(client)
        wait_work_idle()

    assert len(_nudges(client, tid, "pr_review")) == REVIEW_NUDGE_LIMIT
    # 到顶不是安静地放弃：卡面说得出「来回过几轮了、该人看了」。
    assert "需要人" in _cards(client, tid)[0]["note"]


# ---- 验收标准 5：合并冲突回流 ------------------------------------------------


def test_a_conflict_is_announced_once_and_again_on_a_new_commit(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    fake.mergeable_by_number[number] = False

    _poll(client)
    _poll(client)
    wait_work_idle()
    assert len(_nudges(client, tid, "pr_conflict")) == 1

    # 芝士推了一次合并上来，冲突还在 —— 那是新事实，要再说一次。
    moved = "sha-after-merge-attempt"
    fake.prs[number]["head_sha"] = moved
    fake.check_state_by_sha[moved] = ("pending", "等待中：Backend Test")
    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "pr_conflict")) == 2


def test_github_still_computing_mergeability_is_never_read_as_a_conflict(
    client, app_world
):
    """GitHub 算 `mergeable` 是异步的，刚推完那一下它是 null。把 null 当冲突，
    等于每次推送都报一次假冲突。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    fake.mergeable_by_number[number] = None

    _poll(client)
    wait_work_idle()

    assert _nudges(client, tid, "pr_conflict") == []


# ---- 验收标准 6：外部文本进芝士上下文之前先消毒 ------------------------------


def test_control_characters_from_github_never_reach_the_agent(client, app_world):
    """CI 日志、分支名、评论正文都是仓库外的人能控制的字符串，而它们最终会被贴进
    一个跑在 tmux 里的终端。一段清屏转义在任何一份日志里都是隐形的。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = (
        "failure",
        "Backend Test: failure\n\x1b[31m红色\x1b[0m\x1b[2J\x07\x00尾巴",
    )
    fake.reviews_by_number[number] = [
        _review(1, body="改这里\x1b]0;劫持标题栏\x07\x1b[2K")
    ]
    fake.mergeable_by_number[number] = False
    fake.prs[number]["head"] = "topic/\x1b[2Jevil"

    _poll(client)
    wait_work_idle()

    text = _all_text(client, tid)
    assert "\x1b" not in text
    assert "\x07" not in text
    assert "\x00" not in text
    # 洗的是载体不是内容 —— 被转义序列包着的**正文**一个字都不能少。
    assert "红色" in text and "尾巴" in text
    assert "改这里" in text
    # 但 `ESC ] 0 ; … BEL` 里那一段不是正文，它是这条终端指令的**参数**（改标题
    # 栏用的）。连参数一起删掉是对的：留下它等于把一段专门写来骗人的字符串，当成
    # 评论正文展示给读的人。
    assert "劫持标题栏" not in text


# ---- 验收标准 7：2026-08-10 那条修复不许被重新引入 ---------------------------


def test_a_stale_pause_note_still_does_not_swallow_a_ci_failure(client, app_world):
    """2026-08-10 的回归钉：`⚠️ 轮询暂停` 那条 note 曾经把之后每一次 CI 失败都
    静默吞掉（判据是 `startswith("⚠️")`，整个 ⚠️ 家族都算「已经叫过了」）。
    卡面上写着别的东西，绝不能成为闭嘴的理由。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _set_note(client, cid, code=NoteCode.poll_paused, text="轮询暂停，下一轮还会重试")

    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "ci_failed")) == 1


def test_a_repush_failure_still_outranks_a_ci_failure(client, app_world):
    """反过来的那一半：重推失败 / 分支分叉意味着芝士的修复根本没到 GitHub，PR 上
    那片红是旧的。这时候催它再修一遍是催错了对象，必须闭嘴。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _set_note(client, cid, code=NoteCode.repush_failed, text="平台自动重推失败")

    _poll(client)
    wait_work_idle()

    assert _nudges(client, tid, "ci_failed") == []
    assert _cards(client, tid)[0]["note"] == "平台自动重推失败"


def test_a_green_but_conflicted_pr_summons_the_agent_not_the_merge(
    client, app_world
):
    """检查全绿但和 main 冲突（DIRTY）：#718 的表把它派给芝士 —— 冲突事件到
    做活的 agent，而轮询器不去替人调那次注定 409 的合并。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部 9 项检查通过")
    fake.mergeable_by_number[number] = False

    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "pr_conflict")) == 1
    assert fake.merge_calls == []
    # 60s 轮询：同一个冲突不重复叫。
    _poll(client)
    wait_work_idle()
    assert len(_nudges(client, tid, "pr_conflict")) == 1


def test_the_reviews_api_failing_does_not_take_the_ci_failure_down_with_it(
    client, app_world
):
    """收集途中读 GitHub 失败，不能把已经排好队的其它待发一起丢掉 —— 那只是
    「一件事吞掉另一件事」的另一种写法。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    async def _boom(*a, **kw):
        raise github_pr.GitHubPrError("GitHub 拒绝列出评审意见（HTTP 403）")

    fake.review_signals = _boom  # type: ignore[method-assign]

    _poll(client)
    wait_work_idle()

    assert len(_nudges(client, tid, "ci_failed")) == 1
