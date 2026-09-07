"""合并态判定（`merge_state.compute_merge_state` / `local_merge_state`，#718）。

表驱动、全 fake payload、零网络：每一行是「一组 GitHub 信号 + 保护参数 →
期望的 state 和 reason」。断言的是行为（issue #718 的规则表），不是实现。
"""

import pytest

from app.domain.review.merge_state import (
    CheckRun,
    MergeVerdict,
    RequiredCheck,
    compute_merge_state,
    local_merge_state,
    whose_move,
)


def _kinds(verdict: MergeVerdict) -> set[str]:
    return {r.kind for r in verdict.reasons}


def _checks(verdict: MergeVerdict, kind: str) -> tuple[str, ...]:
    for r in verdict.reasons:
        if r.kind == kind:
            return r.checks
    return ()


def _green(name: str) -> CheckRun:
    return CheckRun(name=name, status="completed", conclusion="success")


def _red(name: str) -> CheckRun:
    return CheckRun(name=name, status="completed", conclusion="failure")


def _running(name: str) -> CheckRun:
    return CheckRun(name=name, status="in_progress", conclusion=None)


TEST_GLOB = RequiredCheck(name="test", paths=("backend/**", ".github/workflows/*"))


# ---- github_enforces=True：原样透传，平台一个字不重算 ------------------------
#
# 就算平台的名单/strict 按理会给出别的结论（下面每行都故意配了会翻案的保护
# 参数），GitHub 自己开着保护时它的词就是最终的词。


@pytest.mark.parametrize(
    ("github_word", "expected_state"),
    [
        ("clean", "clean"),
        ("has_hooks", "clean"),  # 带 pre-receive hook 的 clean
        ("unstable", "unstable"),
        ("blocked", "blocked"),
        ("behind", "behind"),
        ("dirty", "dirty"),
        ("draft", "blocked"),
        ("unknown", "unknown"),
        ("HAS_HOOKS", "clean"),  # 大小写不敏感（GraphQL 词表是大写的）
        ("something_new", "unknown"),  # GitHub 加了新词也不至于崩
        (None, "unknown"),
    ],
)
def test_github_enforces_passes_the_verdict_through(github_word, expected_state):
    verdict = compute_merge_state(
        github_mergeable_state=github_word,
        github_mergeable=True,
        check_runs=[_red("test")],  # 平台规则按理该说 blocked——但不轮到它说
        required_checks=[RequiredCheck(name="test")],
        strict=True,
        base_ancestry="behind",  # strict+behind 按理该说 behind——同上
        github_enforces=True,
    )

    assert verdict.state == expected_state
    assert "github_verdict" in _kinds(verdict)


def test_github_enforces_draft_flag_beats_a_stale_word():
    """draft=True 而 mergeable_state 还是旧词时，draft 赢：draft 的合并接口
    必拒，别的词都是上一轮的。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        github_enforces=True,
        draft=True,
    )

    assert verdict.state == "blocked"
    assert "draft" in _kinds(verdict)


def test_github_enforces_still_names_the_red_check_for_the_label():
    """透传不等于哑巴：UNSTABLE 里「CI 在跑」和「检查红了」发给不同的人
    （issue #718 谁的活表格），reason 注解要把名字带出来。state 不变。"""
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_red("lint"), _running("test")],
        github_enforces=True,
    )

    assert verdict.state == "unstable"
    assert _checks(verdict, "check_failed") == ("lint",)
    assert _checks(verdict, "ci_running") == ("test",)


# ---- 平台补位：必跑名单 ------------------------------------------------------


def test_required_check_red_is_blocked_and_named():
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_red("test"), _green("lint")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_failed") == ("test",)


def test_required_check_absent_is_blocked_not_passed():
    """缺席是 pending，不是通过（#465/#468）：名单里的名字没出现在
    check-runs 里就不放行。"""
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_green("lint")],  # 可见的全绿——正是 #465 放行的那种局面
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_missing") == ("test",)


def test_required_check_skipped_counts_as_missing():
    """必跑检查报 skipped = 它对这次改动没真跑过，不算通过。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[CheckRun(name="test", status="completed", conclusion="skipped")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_missing") == ("test",)


def test_required_check_rerun_green_after_red_passes():
    """re-run 绿掉一个红检查正是重跑的意义：同名 runs 里任何一次过了就算过。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[_red("test"), _green("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "clean"


def test_path_scoped_required_check_is_waived_for_untouched_paths():
    """纯前端改动上 `test`（只对 backend/** 生效）永远不会出现——把缺席读作
    「还在等」就是无限等（#470/#483/#485/#486）。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[],
        changed_paths=["frontend/src/App.tsx"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "clean"


def test_unknown_diff_keeps_scoped_required_checks_mandatory():
    """拿不到改动清单时保守处理：放行等于用一次 API 失败换掉整道阀。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[],
        changed_paths=None,
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_missing") == ("test",)


def test_glob_star_does_not_cross_directories():
    """`*` 不跨目录（GitHub Actions paths 语义）：`frontend/*.ts` 命中不了
    `backend/a/b.ts`，否则路径域这道阀等于关掉。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[],
        changed_paths=["backend/a/b.ts"],
        required_checks=[RequiredCheck(name="fe", paths=("frontend/*.ts",))],
    )

    assert verdict.state == "clean"


def test_unconditional_required_check_applies_to_any_change():
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[],
        changed_paths=["docs/README.md"],
        required_checks=[RequiredCheck(name="lint")],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_missing") == ("lint",)


def test_red_and_missing_are_both_reported():
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_red("lint")],
        changed_paths=["backend/app/main.py"],
        required_checks=[RequiredCheck(name="lint"), TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_failed") == ("lint",)
    assert _checks(verdict, "required_check_missing") == ("test",)


def test_required_check_still_running_is_waiting_on_ci():
    """必跑检查报到了、还在跑 → 等 CI。issue 的表把它归在 UNSTABLE，靠
    reason 和「检查红了」分开。"""
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_running("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "unstable"
    assert _checks(verdict, "ci_running") == ("test",)


# ---- 平台补位：strict / behind ----------------------------------------------


@pytest.mark.parametrize("ancestry", ["behind", "diverged"])
def test_strict_and_stale_base_is_behind(ancestry):
    """绿必须绿在当前基线上：behind 和 diverged 都算落后（#468，两个各自绿
    在旧基上的 PR 相加可以是红的）。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        strict=True,
        base_ancestry=ancestry,
    )

    assert verdict.state == "behind"
    assert "behind_base" in _kinds(verdict)


def test_stale_base_without_strict_is_not_behind():
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        strict=False,
        base_ancestry="behind",
    )

    assert verdict.state == "clean"


def test_unreadable_ancestry_does_not_block():
    """ancestry 读不到（None）不拦——不该冻结整条采纳路。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        strict=True,
        base_ancestry=None,
    )

    assert verdict.state == "clean"


def test_a_red_required_check_outranks_behind():
    """红检查比落后更该先说：behind 的出路是平台自动换基，换完 CI 重跑，
    红的照样红——先把要修的东西亮出来。"""
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_red("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
        strict=True,
        base_ancestry="behind",
    )

    assert verdict.state == "blocked"


# ---- 平台补位：dirty / draft / unknown / 放行 --------------------------------


def test_github_dirty_is_dirty():
    verdict = compute_merge_state(
        github_mergeable_state="dirty",
        github_mergeable=False,
    )

    assert verdict.state == "dirty"
    assert "conflict" in _kinds(verdict)


def test_mergeable_false_is_dirty_even_without_the_word():
    verdict = compute_merge_state(
        github_mergeable_state=None,
        github_mergeable=False,
    )

    assert verdict.state == "dirty"


def test_conflict_outranks_everything_else():
    """冲突的 PR 上 CI 说什么都不重要，先解掉。"""
    verdict = compute_merge_state(
        github_mergeable_state="dirty",
        github_mergeable=False,
        check_runs=[_red("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
        strict=True,
        base_ancestry="behind",
    )

    assert verdict.state == "dirty"


def test_mergeable_none_is_not_a_conflict():
    """三值 mergeable 的老规矩：None = GitHub 还没算完，不是冲突。"""
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=None,
    )

    assert verdict.state == "clean"


def test_draft_is_blocked():
    verdict = compute_merge_state(
        github_mergeable_state="draft",
        github_mergeable=True,
    )

    assert verdict.state == "blocked"
    assert "draft" in _kinds(verdict)


def test_draft_flag_without_the_word_is_also_blocked():
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        draft=True,
    )

    assert verdict.state == "blocked"
    assert "draft" in _kinds(verdict)


def test_unstable_with_empty_required_list_is_acceptable():
    """名单为空（保护默认关，#718）：unstable 放行为 unstable——红的没人点名
    必跑，能合。红的是谁仍如实写进 reason，给「谁的活」标签用。"""
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_red("lint")],
        required_checks=[],
    )

    assert verdict.state == "unstable"
    assert _checks(verdict, "check_failed") == ("lint",)


def test_unstable_with_a_red_outside_the_required_list_is_acceptable():
    verdict = compute_merge_state(
        github_mergeable_state="unstable",
        github_mergeable=True,
        check_runs=[_green("test"), _red("codecov")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "unstable"
    assert _checks(verdict, "check_failed") == ("codecov",)


def test_all_rules_green_and_github_clean_is_clean():
    verdict = compute_merge_state(
        github_mergeable_state="clean",
        github_mergeable=True,
        check_runs=[_green("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
        strict=True,
        base_ancestry="ahead",
    )

    assert verdict.state == "clean"


@pytest.mark.parametrize("word", ["blocked", "behind"])
def test_an_unexpected_github_protection_verdict_is_trusted(word):
    """github_enforces=False 却读到 blocked/behind = GitHub 那边其实有我们
    不知道的保护在执行。听它的，不重算。"""
    verdict = compute_merge_state(
        github_mergeable_state=word,
        github_mergeable=True,
    )

    assert verdict.state == word
    assert "github_verdict" in _kinds(verdict)


@pytest.mark.parametrize("word", ["unknown", None])
def test_no_verdict_yet_is_unknown(word):
    """GitHub 还没算完（"unknown"）或 payload 没带（None）→ unknown：
    不可采纳也不报警，下一轮读到真值自然收敛。"""
    verdict = compute_merge_state(
        github_mergeable_state=word,
        github_mergeable=None,
    )

    assert verdict.state == "unknown"
    assert "no_signal" in _kinds(verdict)


def test_required_checks_are_judged_even_while_github_is_undecided():
    """mergeable_state 还没算出来不挡平台自己的阀：红的必跑检查照样 blocked。"""
    verdict = compute_merge_state(
        github_mergeable_state="unknown",
        github_mergeable=None,
        check_runs=[_red("test")],
        changed_paths=["backend/app/main.py"],
        required_checks=[TEST_GLOB],
    )

    assert verdict.state == "blocked"
    assert _checks(verdict, "required_check_failed") == ("test",)


# ---- 未绑 GitHub 的项目 ------------------------------------------------------


@pytest.mark.parametrize(
    ("conflicts", "expected_state", "expected_kind"),
    [
        (True, "dirty", "conflict"),
        (False, "clean", "no_obstacle"),
        (None, "unknown", "no_signal"),
    ],
)
def test_local_merge_state(conflicts, expected_state, expected_kind):
    verdict = local_merge_state(conflicts_with_trunk=conflicts)

    assert verdict.state == expected_state
    assert expected_kind in _kinds(verdict)


# ---- 结果形状 ----------------------------------------------------------------


def test_every_verdict_carries_at_least_one_reason():
    """卡面永远有话可说：任何一种输入组合都不该给出空 reasons。"""
    verdicts = [
        compute_merge_state(github_mergeable_state=w, github_mergeable=True)
        for w in ["clean", "unstable", "blocked", "behind", "dirty", "draft", None]
    ]
    verdicts.append(local_merge_state(conflicts_with_trunk=None))

    assert all(v.reasons for v in verdicts)


# ---- whose_move：「谁的活」那张表（issue #718 卡上的小圈）--------------------


class TestWhoseMove:
    def _verdict(self, **kwargs) -> MergeVerdict:
        return compute_merge_state(**kwargs)

    def test_clean_is_the_humans_move(self):
        v = self._verdict(github_mergeable_state="clean", github_mergeable=True)
        assert whose_move(v) == "human"

    def test_dirty_is_the_agents_move(self):
        v = self._verdict(github_mergeable_state="dirty", github_mergeable=False)
        assert whose_move(v) == "agent"

    def test_behind_is_the_platforms_move(self):
        v = self._verdict(
            github_mergeable_state="unstable",
            github_mergeable=True,
            strict=True,
            base_ancestry="behind",
        )
        assert v.state == "behind"
        assert whose_move(v) == "platform"

    def test_ci_running_is_nobodys_summon(self):
        v = self._verdict(
            github_mergeable_state="unstable",
            github_mergeable=True,
            check_runs=[_running("test")],
            required_checks=[RequiredCheck(name="test")],
        )
        assert v.state == "unstable"
        assert whose_move(v) == "ci"

    def test_required_check_red_summons_the_agent(self):
        v = self._verdict(
            github_mergeable_state="unstable",
            github_mergeable=True,
            check_runs=[_red("test")],
            required_checks=[RequiredCheck(name="test")],
        )
        assert v.state == "blocked"
        assert whose_move(v) == "agent"

    def test_unlisted_red_check_still_summons_the_agent(self):
        # UNSTABLE 检查红了（不在必跑名单）→「芝士处理中」，即便可采纳。
        v = self._verdict(
            github_mergeable_state="unstable",
            github_mergeable=True,
            check_runs=[_red("style")],
        )
        assert v.state == "unstable"
        assert whose_move(v) == "agent"

    def test_required_check_missing_waits_on_ci(self):
        v = self._verdict(
            github_mergeable_state="unstable",
            github_mergeable=True,
            check_runs=[],
            required_checks=[RequiredCheck(name="test")],
        )
        assert v.state == "blocked"
        assert whose_move(v) == "ci"

    def test_draft_is_the_agents_move(self):
        v = self._verdict(
            github_mergeable_state=None, github_mergeable=None, draft=True
        )
        assert v.state == "blocked"
        assert whose_move(v) == "agent"

    def test_passthrough_blocked_with_no_detail_goes_to_the_human(self):
        v = self._verdict(
            github_mergeable_state="blocked",
            github_mergeable=True,
            github_enforces=True,
        )
        assert whose_move(v) == "human"

    def test_passthrough_blocked_with_a_red_check_goes_to_the_agent(self):
        v = self._verdict(
            github_mergeable_state="blocked",
            github_mergeable=True,
            github_enforces=True,
            check_runs=[_red("test")],
        )
        assert whose_move(v) == "agent"

    def test_unknown_is_the_platforms_wait(self):
        v = self._verdict(github_mergeable_state=None, github_mergeable=None)
        assert v.state == "unknown"
        assert whose_move(v) == "platform"

    def test_local_conflict_is_the_agents_move(self):
        assert whose_move(local_merge_state(conflicts_with_trunk=True)) == "agent"

    def test_local_clean_is_the_humans_move(self):
        assert whose_move(local_merge_state(conflicts_with_trunk=False)) == "human"
