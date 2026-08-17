"""Tier-2 required 名单的「按改动范围要求」那半边 (#468 的 2026-08-16 修正)。

名单项本来是一串裸名字，缺席一律读作「还在等」。可是 workflow 自己带路径过滤：
本仓库的 `test` 只在 `backend/**` 上触发，纯前端 PR 上它**永远不会出现** ——
无条件要求它等于让这类卡永远等下去（#483/#485/#486 全绿却要人工去 GitHub 合）。

这里钉的是那条判断本身：条目怎么解析、glob 怎么匹配、什么样的 diff 算命中。
"""

from app.domain.review.services import (
    _diff_touches,
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
