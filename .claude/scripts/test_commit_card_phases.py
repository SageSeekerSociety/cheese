#!/usr/bin/env python3
"""Offline tests for evidence-based first-commit -> first-card accounting."""

import unittest

from commit_card_phases import markdown, measure, measure_line


def at(minute):
    return f"2026-10-08T10:{minute:02d}:00Z"


def line(topic="one"):
    return {"topic": topic, "task": topic, "start": at(10), "end": at(20)}


def event(source, phase, start, end, topic="one"):
    return {"topic": topic, "source": source, "phase": phase,
            "start": at(start) if start is not None else None,
            "end": at(end) if end is not None else None}


class AccountingTests(unittest.TestCase):
    def test_no_records_is_unknown_not_waiting(self):
        result = measure_line(line(), [])
        self.assertEqual(result["exclusive_seconds"]["unknown"], 600)
        self.assertEqual(result["exclusive_seconds"]["explicit_wait"], 0)

    def test_missing_end_is_not_replaced_by_window_end(self):
        result = measure_line(line(), [event("running", "review", 12, None)])
        self.assertEqual(result["inclusive_seconds"]["review"], 0)
        self.assertEqual(result["unbounded_sources"], ["running"])
        self.assertEqual(result["exclusive_seconds"]["unknown"], 600)

    def test_clips_both_sides_of_original_window(self):
        result = measure_line(line(), [event("left", "edit", 5, 12),
                                       event("right", "review", 18, 25)])
        self.assertEqual(result["exclusive_seconds"]["edit"], 120)
        self.assertEqual(result["exclusive_seconds"]["review"], 120)
        self.assertEqual(result["exclusive_seconds"]["unknown"], 360)

    def test_parallel_checks_are_unioned_not_summed(self):
        result = measure_line(line(), [event("a", "local_check", 10, 16),
                                       event("b", "local_check", 14, 20)])
        self.assertEqual(result["inclusive_seconds"]["local_check"], 600)
        self.assertEqual(result["exclusive_seconds"]["local_check"], 600)
        self.assertEqual(result["exclusive_seconds"]["overlap"], 0)

    def test_ci_running_during_edit_is_not_all_ci_wait(self):
        result = measure_line(line(), [event("ci1", "ci", 10, 18),
                                       event("edit1", "edit", 14, 20)])
        self.assertEqual(result["inclusive_seconds"]["ci"], 480)
        self.assertEqual(result["inclusive_seconds"]["edit"], 360)
        self.assertEqual(result["exclusive_seconds"]["ci"], 240)
        self.assertEqual(result["exclusive_seconds"]["edit"], 120)
        self.assertEqual(result["exclusive_seconds"]["overlap"], 240)
        self.assertEqual(result["overlap_seconds"], {"ci + edit": 240})
        self.assertEqual(sum(result["exclusive_seconds"].values()), 600)

    def test_different_tasks_simultaneously_running_each_keep_their_wall_time(self):
        cohort = {"lines": [line(), line("two")]}
        evidence = {"observations": [event("x", "ci", 10, 20),
                                     event("y", "ci", 10, 20, "two")]}
        result = measure(cohort, evidence)
        self.assertEqual(result["wall_seconds"], 1200)
        self.assertEqual(result["exclusive_seconds"]["ci"], 1200)

    def test_touching_boundaries_do_not_create_overlap(self):
        result = measure_line(line(), [event("a", "edit", 10, 15),
                                       event("b", "review", 15, 20)])
        self.assertEqual(result["exclusive_seconds"]["overlap"], 0)
        self.assertEqual(result["exclusive_seconds"]["unknown"], 0)

    def test_duplicate_sources_refuse_instead_of_double_counting(self):
        observation = event("a", "edit", 10, 15)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            measure_line(line(), [observation, observation])

    def test_bad_endpoints_and_foreign_evidence_refuse(self):
        with self.assertRaisesRegex(ValueError, "precedes"):
            measure_line({**line(), "start": at(30)}, [])
        with self.assertRaisesRegex(ValueError, "negative observation"):
            measure_line(line(), [event("a", "ci", 20, 10)])
        with self.assertRaisesRegex(ValueError, "outside"):
            measure({"lines": [line()]}, {"observations": [event("a", "ci", 10, 20, "two")]})
        with self.assertRaisesRegex(ValueError, "timezone"):
            measure_line({**line(), "start": "2026-10-08T10:10:00"}, [])

    def test_report_preserves_unknown_and_non_additive_inclusive_column(self):
        result = measure({"lines": [line()]}, {"observations": []})
        text = markdown(result)
        self.assertIn("拿不到阶段记录 | 10.00 | 100.00%", text)
        self.assertIn("含重叠列不能相加", text)
        self.assertIn("CI 在运行不等于等待 CI", text)


if __name__ == "__main__":
    unittest.main()
