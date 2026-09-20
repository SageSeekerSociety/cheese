"""人工放行留痕的措辞：「当时检查是什么状态」必须写当时真实读到的，
不能把全绿/没读到写成「明知未全绿」（PR #520 在卡上留过一条自相矛盾的历史）。"""

from app.domain.review.services import _force_merge_verdict


class TestForceMergeVerdictWording:
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
