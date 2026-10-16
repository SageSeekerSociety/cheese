"""pr-state.py 的契约：七个状态各判成什么，和它永远不许说出口的两句话。

样本是固定的 `gh pr view` 返回，不打网络、不碰 gh。每条用例问的是一个人能先说出来
的规则 —— 「已合并的 PR 不该再被劝去推」「只是缺检查的 PR 不是冲突」—— 而不是实现
怎么写。最后两条用例故意让一个错的判定实现跑过同一组断言，证明这些断言真的会红：
一条永远不会红的断言和一个没写的断言，读起来一样。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("pr-state.py")
spec = importlib.util.spec_from_file_location("pr_state", SCRIPT)
assert spec and spec.loader
pr_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_state)

HEAD = "0" * 40

# 一句话里出现这些词，就是「照做会出事」的推进建议：对一个已经合并的 PR 说它，等于
# 让作者往一个不会再收提交的分支上白推一轮。
PROCEED_MARKERS = ("可推进", "可采纳", "可合并", "git merge", "git fetch", "继续推", "重做")


def sample(**over) -> dict:
    """一条 OPEN、干净、能合的 `gh pr view` 返回，按需覆盖字段。"""
    payload = {
        "number": 1,
        "state": "OPEN",
        "mergedAt": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "headRefOid": HEAD,
        "isInMergeQueue": False,
    }
    payload.update(over)
    return payload


class OneConclusionPerState(unittest.TestCase):
    def test_merged_stops_the_author(self):
        verdict = pr_state.classify(sample(state="MERGED", mergedAt="2026-10-16T02:51:00Z"))
        self.assertEqual(verdict.code, pr_state.MERGED)
        self.assertEqual(
            verdict.line,
            "MERGED (mergedAt=2026-10-16T02:51:00Z) — 已合并，立即停手，严禁再推提交或重跑",
        )

    def test_closed_is_reported(self):
        verdict = pr_state.classify(sample(state="CLOSED"))
        self.assertEqual(verdict.code, pr_state.CLOSED)
        self.assertEqual(verdict.line, "CLOSED — PR 已关闭")

    def test_conflicting_from_mergeable_gives_the_merge_command(self):
        verdict = pr_state.classify(sample(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertEqual(verdict.code, pr_state.CONFLICTING)
        self.assertIn("git merge --no-autostash origin/main", verdict.line)

    def test_dirty_alone_is_a_conflict_even_before_github_says_conflicting(self):
        verdict = pr_state.classify(sample(mergeable="MERGEABLE", mergeStateStatus="DIRTY"))
        self.assertEqual(verdict.code, pr_state.CONFLICTING)

    def test_in_the_merge_queue_waits_for_the_queue(self):
        verdict = pr_state.classify(sample(isInMergeQueue=True, mergeStateStatus="BLOCKED"))
        self.assertEqual(verdict.code, pr_state.QUEUED)
        self.assertIn("等待队列检查完成", verdict.line)

    def test_blocked_the_gate_is_not_a_conflict(self):
        verdict = pr_state.classify(sample(mergeStateStatus="BLOCKED"))
        self.assertEqual(verdict.code, pr_state.BLOCKED)
        self.assertIn("无代码冲突", verdict.line)

    def test_every_ready_status_reads_as_clean(self):
        for status in pr_state.READY_STATUSES:
            with self.subTest(status=status):
                verdict = pr_state.classify(sample(mergeStateStatus=status))
                self.assertEqual(verdict.code, pr_state.CLEAN)

    def test_unknown_mergeable_asks_to_look_again(self):
        verdict = pr_state.classify(sample(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN"))
        self.assertEqual(verdict.code, pr_state.UNKNOWN)
        self.assertIn("稍后重查", verdict.line)

    def test_an_unrecognised_status_is_never_dressed_up_as_clean(self):
        for status in ("BEHIND", "DRAFT", "SOMETHING_NEW"):
            with self.subTest(status=status):
                verdict = pr_state.classify(sample(mergeStateStatus=status))
                self.assertEqual(verdict.code, pr_state.UNKNOWN)
                self.assertIn(status, verdict.line)

    def test_a_merged_pr_in_the_queue_is_still_merged(self):
        # 队列退出那一刻发出的「离开队列未确认合并」通知，可能就是 PR 已经合掉之后的旧消息。
        verdict = pr_state.classify(
            sample(state="MERGED", mergedAt="2026-10-16T02:51:00Z", isInMergeQueue=True,
                   mergeable="MERGEABLE", mergeStateStatus="BLOCKED")
        )
        self.assertEqual(verdict.code, pr_state.MERGED)


class TheStateIsSettledInOneQuery(unittest.TestCase):
    """判定用到的字段必须都在那一次查询里，少一个就得多问一次 GitHub，多一次旧答案。"""

    def test_the_single_query_carries_every_field_the_decision_needs(self):
        for field in ("state", "mergedAt", "mergeable", "mergeStateStatus", "isInMergeQueue"):
            self.assertIn(field, pr_state.STATE_FIELDS)

    def test_the_query_actually_asks_for_those_fields(self):
        # 字段表被改空、或有人另写一份查询，判定就会拿到 None 而静默变成 UNKNOWN。
        for field in pr_state.STATE_FIELDS:
            self.assertIn(field, pr_state.PR_QUERY)


class NoConflictIsEverReportedAsFine(unittest.TestCase):
    """跨全部输入的一条不变式：说「能推进／缺检查／在队列里」时，冲突必须不存在。"""

    def test_proceed_verdicts_never_appear_on_conflicting_input(self):
        for state in ("OPEN", "MERGED", "CLOSED"):
            for mergeable in ("MERGEABLE", "CONFLICTING", "UNKNOWN"):
                for status in ("CLEAN", "HAS_HOOKS", "UNSTABLE", "BLOCKED", "DIRTY", "BEHIND", "UNKNOWN"):
                    for queued in (False, True):
                        with self.subTest(state=state, mergeable=mergeable, status=status, queued=queued):
                            verdict = pr_state.classify(
                                sample(state=state, mergeable=mergeable,
                                       mergeStateStatus=status, isInMergeQueue=queued)
                            )
                            conflicting = mergeable == "CONFLICTING" or status == "DIRTY"
                            if verdict.code in (pr_state.CLEAN, pr_state.BLOCKED, pr_state.QUEUED):
                                self.assertFalse(conflicting, verdict.line)


class TheGuardsWouldCatchAWrongClassifier(unittest.TestCase):
    """两条故意违规自测：用一个错的实现跑同一组断言，断言必须红。

    断言一次都不红的「硬拦」，和没写是同一件事。这两个错实现恰好是本工具存在的理由 ——
    按队列判断压过合并状态，和把缺检查当成冲突 —— 所以它们必须被拦住。
    """

    def assert_merged_stops_the_author(self, verdict):
        self.assertIn("立即停手", verdict.line)
        for marker in PROCEED_MARKERS:
            self.assertNotIn(marker, verdict.line)

    def assert_not_a_conflict(self, verdict):
        self.assertNotEqual(verdict.code, pr_state.CONFLICTING)
        self.assertNotIn("git merge", verdict.line)

    def test_a_queue_first_classifier_is_caught(self):
        # 故意违规：先看 isInMergeQueue，再看 state —— 已合并的 PR 会被劝去等队列。
        def queue_first(pr):
            if pr.get("isInMergeQueue"):
                return pr_state.Verdict(pr_state.QUEUED, "IN_MERGE_QUEUE — 已在合并队列中，等待队列检查完成")
            return pr_state.classify(pr)

        wrong = queue_first(sample(state="MERGED", mergedAt="2026-10-16T02:51:00Z", isInMergeQueue=True))
        with self.assertRaises(AssertionError):
            self.assert_merged_stops_the_author(wrong)

    def test_a_blocked_is_a_conflict_classifier_is_caught(self):
        # 故意违规：把 BLOCKED 读成「合不了 = 冲突」，就会白重做一遍合并。
        def blocked_is_conflict(pr):
            if pr.get("mergeStateStatus") == "BLOCKED":
                return pr_state.Verdict(
                    pr_state.CONFLICTING,
                    "CONFLICTING — 确有合并冲突，请 git fetch origin main && git merge --no-autostash origin/main",
                )
            return pr_state.classify(pr)

        wrong = blocked_is_conflict(sample(mergeStateStatus="BLOCKED"))
        with self.assertRaises(AssertionError):
            self.assert_not_a_conflict(wrong)


class TheCommandItself(unittest.TestCase):
    """`--json-input` 是离线入口，也让这条命令的对外形状可以被整条跑一遍。"""

    def run_script(self, payload: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pr.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--json-input", str(path)],
                capture_output=True, text=True, check=False,
            )

    def test_it_prints_one_line_and_exits_zero(self):
        result = self.run_script(sample(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.strip(),
            "CONFLICTING — 确有合并冲突，请 git fetch origin main && git merge --no-autostash origin/main",
        )

    def test_a_merged_pr_prints_the_stop_line(self):
        result = self.run_script(sample(state="MERGED", mergedAt="2026-10-16T02:51:00Z"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("立即停手", result.stdout)

    def test_no_argument_is_a_usage_error_not_a_silent_pass(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--json-input", result.stderr)


if __name__ == "__main__":
    unittest.main()
