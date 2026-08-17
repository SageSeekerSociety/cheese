"""Tier-2 required 名单的「按改动范围要求」那半边 (#468 的 2026-08-16 修正)。

名单项本来是一串裸名字，缺席一律读作「还在等」。可是 workflow 自己带路径过滤：
本仓库的 `test` 只在 `backend/**` 上触发，纯前端 PR 上它**永远不会出现** ——
无条件要求它等于让这类卡永远等下去（#483/#485/#486 全绿却要人工去 GitHub 合）。

这里钉的是那条判断本身：条目怎么解析、glob 怎么匹配、什么样的 diff 算命中，
以及**算不出来的时候卡面怎么说**（2026-08-17：等待和保守回退在卡上一个字都不差，
人只能靠后端日志区分，而那份日志的保留期只有「距上次部署多久」）。
"""

from app.domain.review.services import (
    _absent_required_tail,
    _absent_required_timeout,
    _diff_touches,
    _force_merge_verdict,
    _glob_regex,
    _parse_required_checks,
)


class TestParsing:
    def test_a_bare_name_is_required_unconditionally(self):
        [entry] = _parse_required_checks("test")
        assert entry.name == "test"
        assert entry.paths == ()

    def test_a_name_can_carry_the_diff_scope_that_makes_it_required(self):
        [entry] = _parse_required_checks("test:backend/**;.github/workflows/test.yml")
        assert entry.name == "test"
        assert entry.paths == ("backend/**", ".github/workflows/test.yml")

    def test_entries_are_comma_separated_and_may_mix_both_forms(self):
        entries = _parse_required_checks(" test:backend/** , guards ")
        assert [(e.name, e.paths) for e in entries] == [
            ("test", ("backend/**",)),
            ("guards", ()),
        ]

    def test_empty_disables_the_roster(self):
        assert _parse_required_checks("") == []
        assert _parse_required_checks("  ,  ") == []


class TestGlobs:
    def test_double_star_crosses_directories(self):
        assert _glob_regex("backend/**").match("backend/app/domain/x.py")
        assert _glob_regex("backend/**").match("backend/HEAD")
        assert not _glob_regex("backend/**").match("frontend/src/a.vue")

    def test_single_star_does_not_cross_directories(self):
        """`fnmatch` 会在这里说 yes —— 它的 `*` 跨 `/`，用它等于把这道阀关掉。"""
        assert _glob_regex("frontend/*.ts").match("frontend/a.ts")
        assert not _glob_regex("frontend/*.ts").match("frontend/src/a.ts")

    def test_a_literal_path_matches_only_itself(self):
        pat = ".github/workflows/test.yml"
        assert _glob_regex(pat).match(".github/workflows/test.yml")
        assert not _glob_regex(pat).match(".github/workflows/test.yml.bak")
        assert not _glob_regex(pat).match("xgithub/workflows/test.yml")

    def test_question_mark_is_one_character_and_dots_are_literal(self):
        assert _glob_regex("a?.py").match("ab.py")
        assert not _glob_regex("a?.py").match("abc.py")
        assert not _glob_regex("a.py").match("axpy")


class TestDiffMatching:
    BACKEND = ("backend/**", ".github/workflows/test.yml")

    def test_a_backend_change_requires_the_backend_check(self):
        assert _diff_touches(
            self.BACKEND, [("modified", "backend/app/domain/review/services.py")]
        )

    def test_a_frontend_only_change_does_not(self):
        assert not _diff_touches(
            self.BACKEND,
            [
                ("modified", "frontend/src/components/ChatPanel.vue"),
                ("modified", "docs/topics/深色适配.md"),
            ],
        )

    def test_one_matching_file_out_of_many_is_enough(self):
        assert _diff_touches(
            self.BACKEND,
            [
                ("modified", "frontend/src/a.vue"),
                ("added", "backend/tests/unit/test_x.py"),
            ],
        )

    def test_an_empty_diff_matches_nothing(self):
        assert not _diff_touches(self.BACKEND, [])


class TestWaitingWording:
    """「在等一个该出现的检查」和「没算出改动范围、于是保守地仍然要求它」——
    结论一样（都继续等），要人做的事完全不一样，卡面必须分得开。"""

    def test_a_real_wait_says_the_check_has_not_shown_up(self):
        assert _absent_required_tail("test", None) == "required 检查还没出现：test"

    def test_a_fallback_says_it_is_a_fallback_and_why(self):
        tail = _absent_required_tail("test", "GitHub 没给出这次改动的文件清单")
        assert "没能判断这次改动碰了哪些文件" in tail
        assert "GitHub 没给出这次改动的文件清单" in tail  # 具体原因
        assert "test" in tail
        # 关键：不能再声称那项检查「还没出现」——平台根本不知道它该不该出现。
        assert "还没出现" not in tail

    def test_the_two_never_read_the_same(self):
        assert _absent_required_tail("test", None) != _absent_required_tail(
            "test", "认不出这个 PR 要合进哪条分支：RuntimeError: boom"
        )

    def test_a_timeout_on_a_real_wait_blames_the_workflow(self):
        reason, explain = _absent_required_timeout("test", None, 30)
        assert "30 分钟" in reason
        assert "workflow 没被触发、被改名或被禁用" in reason
        assert "没人跑过这项检查" in explain

    def test_a_timeout_in_fallback_does_not_blame_the_workflow(self):
        """回退状态下平台连「这次改动碰没碰后端」都不知道，照搬「多半是 workflow
        被改名了」是把一个没验证过的判断说给人听。"""
        reason, explain = _absent_required_timeout(
            "test", "GitHub 没给出这次改动的文件清单", 30
        )
        assert "30 分钟" in reason
        assert "没能判断这次改动碰了哪些文件" in reason
        assert "GitHub 没给出这次改动的文件清单" in reason
        assert "workflow 没被触发、被改名或被禁用" not in reason
        assert "本来就不会对这次改动触发" in explain


class TestForceMergeWording:
    """人工放行的留痕必须如实反映**当时读到的**检查状态。

    2026-08-17 的 PR #520 在卡上留下过一条自相矛盾的历史：「明知检查未全绿仍
    合并（合并时检查状态：success（全部 5 项检查通过））」。"""

    def test_green_at_merge_is_not_recorded_as_knowingly_red(self):
        verdict = _force_merge_verdict("success")
        assert "未全绿" not in verdict
        assert "全绿" in verdict

    def test_red_at_merge_is_still_recorded_as_knowingly_red(self):
        assert _force_merge_verdict("failure") == "明知检查未全绿仍合并"

    def test_still_running_is_neither_green_nor_red(self):
        verdict = _force_merge_verdict("pending")
        assert "未全绿" not in verdict
        assert "没等检查跑完" in verdict

    def test_no_checks_says_nothing_ever_ran(self):
        assert "没有任何 CI" in _force_merge_verdict("no_checks")

    def test_unreadable_state_says_so_instead_of_guessing_red(self):
        """凭据坏了照样放行（不能把人锁在门外），但那不等于「明知未全绿」。"""
        verdict = _force_merge_verdict(None)
        assert "读不到检查状态" in verdict
        assert "未全绿" not in verdict
