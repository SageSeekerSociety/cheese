"""人类授权动作前移 (2026-08-10) — 人的那一下从「合并前」挪到「开 PR 前」。

拍板依据（@wangchangxin）：「我觉得也可以，给一个设计稿就能要权限了。」人凭摘要
就能授权，不必等 CI 结果。于是那一下买到的是三样东西：PR 开出来、真 CI 这时才
开始跑、**这个 PR 之后的全部迭代都不用再问人**（摩擦 O(1)，不是 O(迭代次数)）。

代价是人点下去的那一刻 CI 一个结果都没有，所以机器后来自动合并之前必须自己守住
三道闸。本文件测的就是这三道闸，外加它们不该误伤的那条主路径：

- 例外 1：授权之后 head 又动，且新 diff 超出授权范围（新增文件 / 碰 `.github/`
  / 碰迁移）——`test_drift_*`。反面：只改人已经看过的文件，照常自动合并
  （`test_iteration_within_authorized_scope_still_auto_merges`），否则 O(1) 的
  承诺就没了。
- 例外 2：CI 从来没真跑过（零检查判出来的绿 ≠ 真跑过测试）——`test_no_checks_*`。
- 例外 3：目标是 prod——`test_prod_base_*`。

复用 test_accept_pr.py 的 fake GitHub 客户端和装配（同一套两阶段采纳的测试替身，
不另起一份），只加本特性需要的状态。
"""

import asyncio

import pytest

from tests.integration.test_accept_pr import (
    _auth,
    _cards_for_topic,
    _make_card,
    _make_project,
    _make_topic,
    _poll,
    _pr_ready,
    _reset_client,
    _topic,
)

# 一份"人授权时看到的" diff：两个已存在文件被改。
AUTHORIZED_DIFF = [
    ("modified", "backend/app/domain/review/services.py"),
    ("modified", "backend/tests/integration/test_accept_pr.py"),
]


def _authorize(client, monkeypatch, *, reviewer: str = "alice") -> tuple:
    """走完"人点授权"这一下，返回 (fake_client, topic_id, card_dict)。"""
    fake = _pr_ready(client, monkeypatch, handle=reviewer)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, reviewer=reviewer)
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": reviewer},
        headers=_auth(reviewer),
    )
    assert r.status_code == 200
    return fake, tid, r.json()["data"]


def _push_fix(fake, card: dict, *, files: list[tuple[str, str]] | None) -> str:
    """芝士 在 PR 上推了一个新提交，新 head 的 PR diff 是 `files`。返回新 sha。"""
    number = card["pr_number"]
    new_sha = fake.push_new_commit(number)
    fake.files_by_sha[new_sha] = files
    fake.check_state_by_sha[new_sha] = ("success", "全部 12 项检查通过")
    return new_sha


# ---- 主路径：人点的那一下发生在开 PR 之前，不是合并之前 ----------------------


def test_click_opens_pr_and_starts_ci_without_merging_anything(client, monkeypatch):
    """人点下去 = 开 PR + 真 CI 这时才开始跑。那一刻平台不能合并任何东西，也不能
    假装已经知道 CI 的结果——这正是"授权"和"验收"的分界。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)

        assert card["status"] == "pr_open"
        assert card["pr_number"] is not None
        # 点下去的那一刻：PR 开了，但一次合并调用都没发生过。
        assert fake.merge_calls == []
        assert card["pr_merged_at"] is None
        # 话题不归档、容器不停——之后的迭代都在这个 PR 上继续。
        assert _topic(client, tid)["status"] == "active"
        # 卡面不能替一段还没被检查过的代码背书。
        assert "真 CI 现在才开始跑" in card["note"]
    finally:
        _reset_client()


def test_ci_unfinished_never_merges(client, monkeypatch):
    """CI 没跑完就不许合并——授权买的是"让 CI 跑"，不是"跳过 CI"。"""
    try:
        fake, tid, _card = _authorize(client, monkeypatch)
        # 没配 check_state → fake 默认 pending。
        _poll(client)
        _poll(client)

        assert fake.merge_calls == []
        after = _cards_for_topic(client, tid)[0]
        assert after["status"] == "pr_open"
        assert after["pr_merged_at"] is None
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_green_within_authorized_scope_auto_merges_without_asking_again(
    client, monkeypatch
):
    """三道闸都不命中时，行为跟改动之前一模一样：全绿即自动合并，不再问人。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[head] = AUTHORIZED_DIFF
        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-1"

        _poll(client)

        assert len(fake.merge_calls) == 1
        after = _cards_for_topic(client, tid)[0]
        assert after["pr_merged_at"] is not None
        assert "✋" not in after["note"]
    finally:
        _reset_client()


def test_iteration_within_authorized_scope_still_auto_merges(client, monkeypatch):
    """摩擦必须是 O(1)：授权之后芝士继续改**人已经看过的那些文件**（修 CI 报错的
    常态），不算漂移，照常自动合并。把这条也拦下来，等于把摩擦变回
    O(迭代次数)，整个方案就没意义了。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        authorized_head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[authorized_head] = AUTHORIZED_DIFF
        # 新提交只动了授权 diff 里已有的文件。
        _push_fix(fake, card, files=AUTHORIZED_DIFF)
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-2"

        _poll(client)

        assert len(fake.merge_calls) == 1
        after = _cards_for_topic(client, tid)[0]
        assert after["pr_merged_at"] is not None
    finally:
        _reset_client()


# ---- 例外 1：授权之后 head 又动，新 diff 超出授权范围 ------------------------


@pytest.mark.parametrize(
    ("drifted", "expected_phrase"),
    [
        (
            [*AUTHORIZED_DIFF, ("added", "backend/app/domain/review/sneaky.py")],
            "新增了文件",
        ),
        (
            [*AUTHORIZED_DIFF, ("modified", ".github/workflows/build.yml")],
            "动了 CI 配置",
        ),
        (
            [*AUTHORIZED_DIFF, ("added", "backend/alembic/versions/abc123_x.py")],
            "新增了文件",
        ),
        (
            [*AUTHORIZED_DIFF, ("modified", "backend/alembic/versions/old_one.py")],
            "动了数据库迁移",
        ),
    ],
    ids=["new-file", "github-workflows", "new-migration", "edited-migration"],
)
def test_drift_beyond_authorized_scope_blocks_auto_merge(
    client, monkeypatch, drifted, expected_phrase
):
    """例外 1：检查全绿也不自动合并——人当初批的是另一份 diff。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        authorized_head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[authorized_head] = AUTHORIZED_DIFF
        _push_fix(fake, card, files=drifted)

        _poll(client)

        assert fake.merge_calls == []  # 没有合并
        after = _cards_for_topic(client, tid)[0]
        assert after["status"] == "pr_open"
        assert after["pr_merged_at"] is None
        assert after["note"].startswith("✋")
        assert "超出了当时授权的范围" in after["note"]
        assert expected_phrase in after["note"]
        # 回来找人：卡面必须说清人能做什么，而不只是"卡住了"。
        assert "撤销这次采纳" in after["note"]
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_drift_note_is_not_repeated_every_poll(client, monkeypatch):
    """轮询 60 秒一次，同一个原因不能刷屏。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        authorized_head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[authorized_head] = AUTHORIZED_DIFF
        _push_fix(fake, card, files=[*AUTHORIZED_DIFF, ("added", "a/b/new.py")])

        _poll(client)
        first = _cards_for_topic(client, tid)[0]["note"]
        _poll(client)
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"] == first
        assert fake.merge_calls == []
    finally:
        _reset_client()


def test_drift_that_goes_away_merges_again_without_a_human(client, monkeypatch):
    """安全阀不是死路：芝士把越界的东西撤掉之后，范围重新落回授权内，机器继续
    自动合并（漂移是相对"当初授权的那份 diff"算的，不是一旦红过就永远红）。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        authorized_head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[authorized_head] = AUTHORIZED_DIFF
        _push_fix(fake, card, files=[*AUTHORIZED_DIFF, ("added", "a/b/new.py")])
        _poll(client)
        assert fake.merge_calls == []

        # 芝士 删掉了那个新增文件，重新推。
        _push_fix(fake, card, files=AUTHORIZED_DIFF)
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-3"
        _poll(client)

        assert len(fake.merge_calls) == 1
        assert _cards_for_topic(client, tid)[0]["pr_merged_at"] is not None
    finally:
        _reset_client()


def test_unknowable_scope_fails_closed(client, monkeypatch):
    """GitHub 给不出完整文件列表（diff 太大，compare API 300 文件上限）时，
    "范围不明"必须按"要问人"处理，不能按"没变化"放行。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        authorized_head = fake.prs[card["pr_number"]]["head_sha"]
        fake.files_by_sha[authorized_head] = AUTHORIZED_DIFF
        _push_fix(fake, card, files=None)  # None = GitHub 没返回 files

        _poll(client)

        assert fake.merge_calls == []
        note = _cards_for_topic(client, tid)[0]["note"]
        assert note.startswith("✋")
        assert "无法确认" in note
    finally:
        _reset_client()


# ---- 例外 2：CI 从来没真跑过 ------------------------------------------------


def test_no_checks_green_does_not_earn_auto_merge(client, monkeypatch):
    """零检查判出来的"绿"不等于真跑过测试。零检查死锁的修复保持原样（轮询照样
    停下来，不再无限等待），但这种绿不享受免人自动合并。"""
    try:
        fake, tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.check_state_by_sha[head] = (
            "no_checks",
            "没有任何 workflow 会对这次改动触发检查（真 CI 从未跑过）",
        )

        _poll(client)

        assert fake.merge_calls == []
        after = _cards_for_topic(client, tid)[0]
        assert after["status"] == "pr_open"
        assert after["note"].startswith("✋")
        assert "没有任何 CI 真的跑过这次改动" in after["note"]
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_all_checks_passed_is_still_distinguished_from_nothing_ran(client, monkeypatch):
    """同一张卡、同一个 head：`success` 合，`no_checks` 不合。这条就是例外 2 的
    全部内容——把"12 项全过"和"没有 workflow 会触发"分开。"""
    try:
        fake, _tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.check_state_by_sha[head] = ("no_checks", "没有 workflow 会跑")
        _poll(client)
        assert fake.merge_calls == []

        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-4"
        _poll(client)
        assert len(fake.merge_calls) == 1
    finally:
        _reset_client()


# ---- 例外 3：目标是 prod ----------------------------------------------------


def test_prod_base_never_auto_merges(client, monkeypatch):
    """prod 永远两次都要人。本项目当前采纳目标是 main/dev，这条是把判断位留出来
    ——但它必须是真的会拦住，而不是一句注释。"""
    from app.domain.workspace import service as ws

    try:
        fake, tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        monkeypatch.setattr(ws, "pr_base_branch", lambda _pid: "prod")

        _poll(client)

        assert fake.merge_calls == []
        after = _cards_for_topic(client, tid)[0]
        assert after["note"].startswith("✋")
        assert "prod" in after["note"]
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_main_base_is_not_treated_as_prod(client, monkeypatch):
    """反面：main 不是 prod，别把主路径也拦了。"""
    try:
        fake, _tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-5"

        _poll(client)

        assert len(fake.merge_calls) == 1
    finally:
        _reset_client()


def test_unresolvable_base_branch_fails_closed(client, monkeypatch):
    """认不出目标分支就不敢替人决定——宁可回来找人，也不能猜。"""
    from app.domain.workspace import service as ws

    def boom(_pid):
        raise RuntimeError("workspace gone")

    try:
        fake, tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]
        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        monkeypatch.setattr(ws, "pr_base_branch", boom)

        _poll(client)

        assert fake.merge_calls == []
        assert _cards_for_topic(client, tid)[0]["note"].startswith("✋")
    finally:
        _reset_client()


# ---- 兼容：已经在途的 pr_open 卡不能被这次改动打断 ---------------------------


def test_in_flight_card_without_authorization_baseline_is_not_interrupted(
    client, monkeypatch
):
    """改动上线时已经在 `pr_open` 的卡，DB 里没有 `pr_authorized_sha`（列是新加
    的、可空、不回填）。这种卡必须照常走完：轮询把它当下骑着的 head 认作基线，
    只能管住之后的推送，绝不能回溯地拦住它。"""
    from sqlalchemy import text

    try:
        fake, tid, card = _authorize(client, monkeypatch)
        head = fake.prs[card["pr_number"]]["head_sha"]

        # 把这张卡改造成"改动上线前就已经在途"的样子。
        async def _clear_baseline() -> None:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                await session.execute(
                    text(
                        "UPDATE accept_cards SET pr_authorized_sha = NULL "
                        "WHERE id = :cid"
                    ),
                    {"cid": card["id"]},
                )
                await session.commit()

        asyncio.run(_clear_baseline())

        fake.check_state_by_sha[head] = ("success", "全部 12 项检查通过")
        fake.merge_sha_by_number[card["pr_number"]] = "merge-sha-6"

        _poll(client)

        assert len(fake.merge_calls) == 1
        assert _cards_for_topic(client, tid)[0]["pr_merged_at"] is not None
    finally:
        _reset_client()
