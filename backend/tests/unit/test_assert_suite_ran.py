"""The gate every suite-running job ends with: did the suite actually run?"""

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.assert_shards_complete import assert_reports_complete
from scripts.assert_shards_complete import main as shards_main
from scripts.assert_suite_ran import (
    SuiteDidNotRun,
    assert_suite_ran,
    main,
    not_clean,
    read_plan,
)


def report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>'
    )
    return path


def selection(
    tmp_path: Path, selected: list[str], *, index: int = 0, count: int = 1
) -> Path:
    directory = tmp_path / "selection"
    directory.mkdir()
    assigned = [
        nodeid
        for nodeid in selected
        if int.from_bytes(hashlib.sha256(nodeid.encode()).digest()) % count == index
    ]
    (directory / "selection-main.json").write_text(
        json.dumps(
            {
                "shard_index": index,
                "shard_count": count,
                "markexpr": "pure",
                "keyword": "not kotlin",
                "selected": selected,
                "assigned": assigned,
            }
        )
    )
    return directory


def passed_nodeid(nodeid: str) -> str:
    return (
        '<testcase classname="tests.unit.test_a" name="test_one">'
        '<properties><property name="cheese_nodeid" '
        f'value="{nodeid}"/></properties></testcase>'
    )


PASSED = '<testcase classname="tests.unit.test_a" name="test_one"/>'
FAILED = (
    '<testcase classname="tests.unit.test_a" name="test_two">'
    '<failure message="nope"/></testcase>'
)
SKIPPED = (
    '<testcase classname="tests.unit.test_a" name="test_three">'
    '<skipped message="Codex binary required"/></testcase>'
)
ERRORED = (
    '<testcase classname="tests.unit.test_a" name="test_four">'
    '<error message="fixture blew up"/></testcase>'
)


def test_a_full_run_passes_the_gate(tmp_path: Path) -> None:
    assert assert_suite_ran(report(tmp_path, PASSED * 3), at_least=3) == 3


def test_a_failed_case_still_counts_as_having_run(tmp_path: Path) -> None:
    # A red case told us something; the gate is about silence, not about green.
    assert assert_suite_ran(report(tmp_path, PASSED + FAILED), at_least=2) == 2


def test_one_skipped_case_fails_the_gate(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED * 5 + SKIPPED), at_least=1)
    assert "test_three" in str(raised.value)
    assert "Codex binary required" in str(raised.value)


def test_a_case_that_errored_in_setup_did_not_run(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED + ERRORED), at_least=2)
    assert "fixture blew up" in str(raised.value)


def test_a_collapsed_selection_fails_even_though_every_case_passed(
    tmp_path: Path,
) -> None:
    # #1236: the canary was green for two days on one test out of eight.
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED), at_least=8)
    assert "at least 8" in str(raised.value)


def test_a_report_that_was_never_written_fails_the_gate(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(tmp_path / "missing.xml", at_least=1)
    assert "does not exist" in str(raised.value)


def test_an_unreadable_report_fails_the_gate(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text("<testsuites><testsuite>")
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(path, at_least=1)


def test_a_shard_runs_every_case_its_manifest_assigned(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    manifests = selection(tmp_path, selected)
    assert (
        assert_suite_ran(
            report(tmp_path, "".join(passed_nodeid(nodeid) for nodeid in selected)),
            at_least=2,
            selection_dir=manifests,
        )
        == 2
    )


def test_a_shard_with_a_missing_junit_case_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    manifests = selection(tmp_path, selected)
    with pytest.raises(SuiteDidNotRun, match="manifest assigned 2"):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(selected[0])),
            at_least=2,
            selection_dir=manifests,
        )


def test_a_collapsed_full_layer_manifest_fails_the_floor(tmp_path: Path) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    manifests = selection(tmp_path, [nodeid])
    with pytest.raises(SuiteDidNotRun, match="at least 8"):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(nodeid)),
            at_least=8,
            selection_dir=manifests,
        )


def test_missing_selection_manifests_fail_closed(tmp_path: Path) -> None:
    directory = tmp_path / "selection"
    directory.mkdir()
    with pytest.raises(SuiteDidNotRun, match="no selection manifests"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_worker_manifests_must_agree(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one"]
    directory = selection(tmp_path, selected)
    manifest = json.loads((directory / "selection-main.json").read_text())
    manifest["keyword"] = "different"
    (directory / "selection-gw0.json").write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="disagrees"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_manifest_rejects_duplicate_nodeids(tmp_path: Path) -> None:
    directory = selection(tmp_path, ["tests/unit/test_a.py::test_one"])
    path = directory / "selection-main.json"
    manifest = json.loads(path.read_text())
    manifest["selected"] *= 2
    path.write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="unique"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_manifest_rejects_the_wrong_partition(tmp_path: Path) -> None:
    selected = [f"tests/unit/test_a.py::test_{number}" for number in range(10)]
    directory = selection(tmp_path, selected, index=0, count=2)
    path = directory / "selection-main.json"
    manifest = json.loads(path.read_text())
    manifest["assigned"] = selected
    path.write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="do not match shard"):
        assert_suite_ran(
            report(tmp_path, PASSED * 10), at_least=2, selection_dir=directory
        )


def test_equal_size_wrong_junit_identity_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one"]
    directory = selection(tmp_path, selected)
    wrong = passed_nodeid("tests/unit/test_a.py::test_other")
    with pytest.raises(SuiteDidNotRun, match="unexpected"):
        assert_suite_ran(report(tmp_path, wrong), at_least=1, selection_dir=directory)


def test_duplicate_junit_identity_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    directory = selection(tmp_path, selected)
    repeated = passed_nodeid(selected[0]) * 2
    with pytest.raises(SuiteDidNotRun, match="duplicate cheese_nodeid"):
        assert_suite_ran(
            report(tmp_path, repeated), at_least=2, selection_dir=directory
        )


# What pytest's JUnit writer leaves for the report xdist sends when a worker
# dies mid-case (recorded from a real run); the retry reports the case again.
CRASH_PLACEHOLDER = '<testcase time="0.000"/>'
QUARANTINED = (
    '<testcase classname="tests.unit.test_a" name="test_five">'
    '<skipped type="pytest.xfail" '
    'message="quarantined: https://github.com/o/r/issues/1"/></testcase>'
)


def reruns(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "reruns.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def test_a_crash_placeholder_needs_a_recorded_crash(tmp_path: Path) -> None:
    results = report(tmp_path, PASSED + CRASH_PLACEHOLDER)
    with pytest.raises(SuiteDidNotRun, match="nameless"):
        assert_suite_ran(results, at_least=1)
    retried = reruns(
        tmp_path, {"nodeid": "tests/unit/test_a.py::test_one", "crashed": True}
    )
    assert assert_suite_ran(results, at_least=1, reruns=retried) == 1


def test_a_failed_retry_does_not_explain_a_placeholder(tmp_path: Path) -> None:
    retried = reruns(
        tmp_path, {"nodeid": "tests/unit/test_a.py::test_one", "crashed": False}
    )
    with pytest.raises(SuiteDidNotRun, match="0 recorded worker crash"):
        assert_suite_ran(
            report(tmp_path, PASSED + CRASH_PLACEHOLDER), at_least=1, reruns=retried
        )


def test_a_quarantined_case_ran_and_is_named(tmp_path: Path, monkeypatch) -> None:
    nodeid = "tests/unit/test_a.py::test_five"
    listing = tmp_path / "quarantine.txt"
    listing.write_text(f"{nodeid} # https://github.com/o/r/issues/1\n")
    monkeypatch.setattr("scripts.assert_suite_ran._QUARANTINE", listing)
    case = QUARANTINED.replace(
        "<skipped",
        '<properties><property name="cheese_nodeid" '
        f'value="{nodeid}"/></properties><skipped',
    )
    results = report(tmp_path, PASSED + case)
    assert assert_suite_ran(results, at_least=2) == 2
    [line] = not_clean(results, None)
    assert "test_five is quarantined" in line


@pytest.mark.parametrize(
    "identity", ["unlisted", "wrong_issue", "missing", "duplicate"]
)
def test_a_quarantine_reason_alone_does_not_authorize_a_skip(
    tmp_path: Path, identity: str, monkeypatch
) -> None:
    nodeid = "tests/unit/test_a.py::test_five"
    listing = tmp_path / "quarantine.txt"
    issue = (
        "https://github.com/o/r/issues/2"
        if identity == "wrong_issue"
        else "https://github.com/o/r/issues/1"
    )
    listing.write_text("" if identity == "unlisted" else f"{nodeid} # {issue}\n")
    monkeypatch.setattr("scripts.assert_suite_ran._QUARANTINE", listing)
    case = QUARANTINED
    if identity != "missing":
        prop = f'<property name="cheese_nodeid" value="{nodeid}"/>'
        props = prop * (2 if identity == "duplicate" else 1)
        case = case.replace("<skipped", f"<properties>{props}</properties><skipped")
    results = report(tmp_path, PASSED + case)
    with pytest.raises(SuiteDidNotRun, match="skipped"):
        assert_suite_ran(results, at_least=2)


def test_an_ordinary_xfail_is_still_a_skip(tmp_path: Path) -> None:
    xfail = QUARANTINED.replace("quarantined: ", "")
    with pytest.raises(SuiteDidNotRun, match="skipped"):
        assert_suite_ran(report(tmp_path, PASSED + xfail), at_least=1)


def test_only_a_retry_that_passed_is_named(tmp_path: Path) -> None:
    one, two = "tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"
    failed_two = (
        '<testcase classname="tests.unit.test_a" name="test_two">'
        f'<properties><property name="cheese_nodeid" value="{two}"/></properties>'
        '<failure message="nope"/></testcase>'
    )
    retried = reruns(
        tmp_path,
        {"nodeid": one, "crashed": False, "reason": "flaky"},
        {"nodeid": two, "crashed": False, "reason": "broken"},
    )
    lines = not_clean(report(tmp_path, passed_nodeid(one) + failed_two), retried)
    assert lines == [f"{one} passed only on a retry; first it failed: flaky"]


@pytest.mark.parametrize("phase,copies", [("call", 2), ("setup", 2), ("teardown", 3)])
def test_retries_do_not_explain_unrelated_duplicate_results(
    tmp_path: Path, phase: str, copies: int
) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    directory = selection(tmp_path, [nodeid])
    retried = reruns(tmp_path, {"nodeid": nodeid, "when": phase})
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(nodeid) * copies),
            at_least=1,
            selection_dir=directory,
            reruns=retried,
        )


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_a_teardown_retry_never_hides_a_bad_result(
    tmp_path: Path, outcome: str
) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    directory = selection(tmp_path, [nodeid])
    retried = reruns(tmp_path, {"nodeid": nodeid, "when": "teardown"})
    bad = passed_nodeid(nodeid).replace("</testcase>", f"<{outcome}/></testcase>")
    results = report(tmp_path, bad + passed_nodeid(nodeid))
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(results, at_least=1, selection_dir=directory, reruns=retried)


# A local run has no CI artifact tree and no shard assignment: it has a report,
# and whatever the person running it said they were going to run.
def plan(tmp_path: Path, nodeids: list[str], *, name: str = "plan.txt") -> Path:
    path = tmp_path / name
    path.write_text("".join(f"{nodeid}\n" for nodeid in nodeids))
    return path


def judge_against_plan(results: Path, planned: Path) -> int:
    return assert_suite_ran(results, expected=planned)


def skips_everything(nodeids: list[str]) -> str:
    return "".join(
        '<testcase classname="tests.unit.test_a" name="test_one">'
        '<properties><property name="cheese_nodeid" '
        f'value="{nodeid}"/></properties>'
        '<skipped message="Codex binary required"/></testcase>'
        for nodeid in nodeids
    )


def all_skipped_run(tmp_path: Path) -> tuple[Path, Path]:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    return report(tmp_path, skips_everything(nodeids)), plan(tmp_path, nodeids)


def refuses_a_silent_run(
    judge: Callable[[Path, Path], int], results: Path, planned: Path
) -> None:
    with pytest.raises(SuiteDidNotRun, match="ran nothing"):
        judge(results, planned)


def test_the_planned_cases_are_what_makes_a_local_run_green(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    results = report(tmp_path, "".join(passed_nodeid(nodeid) for nodeid in nodeids))
    assert assert_suite_ran(results, expected=plan(tmp_path, nodeids)) == 2


def test_a_plan_catches_what_a_floor_would_have_passed(tmp_path: Path) -> None:
    # The local trap: half the selection quietly stopped being collected, and
    # every case that did run passed. A count cannot tell that from a full run.
    nodeids = [f"tests/unit/test_a.py::test_{number}" for number in range(3)]
    results = report(tmp_path, passed_nodeid(nodeids[0]))
    with pytest.raises(SuiteDidNotRun, match="2 missing"):
        assert_suite_ran(results, expected=plan(tmp_path, nodeids))
    assert assert_suite_ran(results, at_least=1) == 1  # the floor alone cannot


def test_a_plan_names_the_cases_it_did_not_see(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    results = report(tmp_path, passed_nodeid(nodeids[0]))
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(results, expected=plan(tmp_path, nodeids))
    assert nodeids[1] in str(raised.value)
    assert "0 unexpected []" in str(raised.value)


def test_a_plan_rejects_a_case_that_was_not_supposed_to_run(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    results = report(
        tmp_path, passed_nodeid(nodeids[0]) + passed_nodeid("tests/unit/test_b.py::x")
    )
    with pytest.raises(SuiteDidNotRun, match="1 unexpected"):
        assert_suite_ran(results, expected=plan(tmp_path, nodeids))


def test_a_report_that_ran_nothing_never_matches_a_plan(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    with pytest.raises(SuiteDidNotRun, match="ran nothing"):
        assert_suite_ran(report(tmp_path, ""), expected=plan(tmp_path, nodeids))


def test_the_plan_can_be_the_selection_manifests_ci_already_writes(
    tmp_path: Path,
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    manifests = selection(tmp_path, nodeids)
    results = report(tmp_path, passed_nodeid(nodeids[0]))
    assert assert_suite_ran(results, expected=manifests) == 1


def test_a_plan_that_names_nothing_fails_closed(tmp_path: Path) -> None:
    path = plan(tmp_path, [], name="plan.json")
    path.write_text("[]")
    with pytest.raises(SuiteDidNotRun, match="at least one"):
        read_plan(path)


def test_a_report_without_case_identity_cannot_be_checked_against_a_plan(
    tmp_path: Path,
) -> None:
    # -p scripts.ci_shard is what makes a local report say which case each
    # result belongs to; without it a green report has no case names at all.
    nodeids = ["tests/unit/test_a.py::test_one"]
    with pytest.raises(SuiteDidNotRun, match="cheese_nodeid"):
        assert_suite_ran(report(tmp_path, PASSED), expected=plan(tmp_path, nodeids))


def test_every_case_skipped_is_refused_as_having_verified_nothing(
    tmp_path: Path,
) -> None:
    refuses_a_silent_run(judge_against_plan, *all_skipped_run(tmp_path))


def test_the_all_skip_rule_goes_red_on_an_implementation_that_passes_skips(
    tmp_path: Path, monkeypatch
) -> None:
    """Plant the violation the rule above exists to catch.

    The mutant reads every skipped case as a quarantined pass — exactly the
    implementation that would report an all-skipped suite green. The rule must
    fail against it; a rule that stayed green would be watching the report's
    shape instead of its behaviour.
    """
    monkeypatch.setattr(
        "scripts.assert_suite_ran._is_quarantined",
        lambda case, quarantine: case.find("skipped") is not None,
    )
    with pytest.raises(pytest.fail.Exception):
        refuses_a_silent_run(judge_against_plan, *all_skipped_run(tmp_path))


def test_a_mutant_that_passes_skips_is_what_the_rule_would_otherwise_miss(
    tmp_path: Path, monkeypatch
) -> None:
    # The other half of the pair above: the mutant really does let the report
    # through, so the rule's red is caused by the mutated decision and nothing
    # else.
    results, planned = all_skipped_run(tmp_path)
    monkeypatch.setattr(
        "scripts.assert_suite_ran._is_quarantined",
        lambda case, quarantine: case.find("skipped") is not None,
    )
    assert judge_against_plan(results, planned) == 2


def test_the_offline_cli_judges_a_report_against_a_plan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    results = report(tmp_path, "".join(passed_nodeid(nodeid) for nodeid in nodeids))
    planned = plan(tmp_path, nodeids)
    monkeypatch.setattr(
        sys, "argv", ["assert_suite_ran", str(results), "--expected", str(planned)]
    )
    assert main() == 0
    assert f"2 case(s) ran, none skipped (matching the plan {planned})" in (
        capsys.readouterr().out
    )


def test_the_offline_cli_names_the_case_the_report_is_missing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    results = report(tmp_path, passed_nodeid(nodeids[0]))
    planned = plan(tmp_path, nodeids)
    monkeypatch.setattr(
        sys, "argv", ["assert_suite_ran", str(results), "--expected", str(planned)]
    )
    assert main() == 1
    assert nodeids[1] in capsys.readouterr().out


# Two shards of one local run, judged together against the plan they meant to
# cover. This is where a shard that silently ran half its cases gets named.
def shard_report(tmp_path: Path, name: str, nodeids: list[str]) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    path = directory / f"{name}-results.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        + "".join(passed_nodeid(nodeid) for nodeid in nodeids)
        + "</testsuite></testsuites>"
    )
    return path


def test_two_shards_covering_their_plan_are_complete(tmp_path: Path) -> None:
    nodeids = [f"tests/unit/test_a.py::test_{number}" for number in range(4)]
    first = shard_report(tmp_path, "first", nodeids[:2])
    second = shard_report(tmp_path, "second", nodeids[2:])
    assert assert_reports_complete([first, second], plan(tmp_path, nodeids)) == 4


def test_a_shard_that_ran_half_its_plan_is_incomplete(tmp_path: Path) -> None:
    nodeids = [f"tests/unit/test_a.py::test_{number}" for number in range(4)]
    first = shard_report(tmp_path, "first", nodeids[:2])
    with pytest.raises(SuiteDidNotRun, match="2 missing"):
        assert_reports_complete([first], plan(tmp_path, nodeids))


def test_one_case_in_two_reports_is_not_coverage(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    second = shard_report(tmp_path, "second", nodeids)
    with pytest.raises(SuiteDidNotRun, match="more than one report"):
        assert_reports_complete([first, second], plan(tmp_path, nodeids))


def test_the_reports_can_be_directories(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    assert assert_reports_complete([first.parent], plan(tmp_path, nodeids)) == 1


def test_a_report_with_a_case_the_plan_does_not_name_is_not_coverage(
    tmp_path: Path,
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", [*nodeids, "tests/unit/test_b.py::x"])
    with pytest.raises(SuiteDidNotRun, match="1 unexpected"):
        assert_reports_complete([first], plan(tmp_path, nodeids))


def test_an_empty_shard_does_not_refuse_a_run_that_covered_its_plan(
    tmp_path: Path,
) -> None:
    # More shards than cases: pytest writes a valid report holding no case.
    # The plan's coverage is what decides, not each shard on its own.
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    empty = shard_report(tmp_path, "second", [])
    assert assert_reports_complete([first, empty], plan(tmp_path, nodeids)) == 1


def test_reports_that_hold_no_case_verified_nothing(tmp_path: Path) -> None:
    # Every report is empty, so the run verified nothing — refused on the spot,
    # rather than only because its plan happens to name cases.
    nodeids = ["tests/unit/test_a.py::test_one"]
    empty = shard_report(tmp_path, "first", [])
    with pytest.raises(SuiteDidNotRun, match="verified nothing"):
        assert_reports_complete([empty], plan(tmp_path, nodeids))


def test_a_case_the_local_run_retried_is_judged_on_its_final_attempt(
    tmp_path: Path,
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    first = shard_report(tmp_path, "first", [nodeids[0], nodeids[0], nodeids[1]])
    recorded = reruns(
        tmp_path, {"nodeid": nodeids[0], "when": "teardown", "reason": "fixture died"}
    )
    with pytest.raises(SuiteDidNotRun, match="duplicate"):
        assert_reports_complete([first], plan(tmp_path, nodeids))
    assert (
        assert_reports_complete([first], plan(tmp_path, nodeids), reruns=recorded) == 2
    )


def test_one_report_named_by_two_spellings_is_read_once(
    tmp_path: Path, monkeypatch
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    monkeypatch.chdir(first.parent)
    reports = [first.parent, Path(first.name)]
    assert assert_reports_complete(reports, plan(tmp_path, nodeids)) == 1


def test_a_report_reached_through_a_symlink_is_read_once(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    link = tmp_path / "link.xml"
    link.symlink_to(first)
    assert assert_reports_complete([first, link], plan(tmp_path, nodeids)) == 1


def test_a_report_that_does_not_exist_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun, match="does not exist"):
        assert_reports_complete(
            [tmp_path / "missing.xml"],
            plan(tmp_path, ["tests/unit/test_a.py::test_one"]),
        )


def test_a_directory_holding_no_report_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "no-reports"
    empty.mkdir()
    with pytest.raises(SuiteDidNotRun, match="holds no JUnit report"):
        assert_reports_complete(
            [empty], plan(tmp_path, ["tests/unit/test_a.py::test_one"])
        )


def test_the_offline_shard_cli_judges_the_reports_together(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    first = shard_report(tmp_path, "first", nodeids)
    planned = plan(tmp_path, nodeids)
    monkeypatch.setattr(
        sys,
        "argv",
        ["assert_shards_complete", "--results", str(first), "--plan", str(planned)],
    )
    shards_main()
    assert "All 1 planned cases ran on exactly one runner." in capsys.readouterr().out


def test_the_offline_shard_cli_passes_the_retry_record_through(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    nodeids = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    first = shard_report(tmp_path, "first", [nodeids[0], nodeids[0], nodeids[1]])
    recorded = reruns(
        tmp_path, {"nodeid": nodeids[0], "when": "teardown", "reason": "fixture died"}
    )
    planned = plan(tmp_path, nodeids)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assert_shards_complete",
            "--results",
            str(first),
            "--plan",
            str(planned),
            "--reruns",
            str(recorded),
        ],
    )
    shards_main()
    assert "All 2 planned cases ran" in capsys.readouterr().out


def test_the_offline_shard_cli_errors_on_a_report_that_does_not_exist(
    tmp_path: Path, monkeypatch
) -> None:
    planned = plan(tmp_path, ["tests/unit/test_a.py::test_one"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["assert_shards_complete", "--results", str(tmp_path / "no.xml")]
        + ["--plan", str(planned)],
    )
    with pytest.raises(SystemExit) as raised:
        shards_main()
    assert "does not exist" in str(raised.value)


def test_the_plan_can_be_one_manifest_or_a_json_list(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    manifest = selection(tmp_path, nodeids) / "selection-main.json"
    assert read_plan(manifest) == nodeids
    listed = tmp_path / "plan.json"
    listed.write_text(json.dumps(nodeids))
    assert read_plan(listed) == nodeids


def test_a_plan_that_names_one_case_twice_fails_closed(tmp_path: Path) -> None:
    planned = plan(tmp_path, ["tests/unit/test_a.py::test_one"] * 2)
    with pytest.raises(SuiteDidNotRun, match="more than once"):
        read_plan(planned)


def test_a_plan_with_a_byte_order_mark_still_names_its_cases(tmp_path: Path) -> None:
    nodeids = ["tests/unit/test_a.py::test_one"]
    planned = tmp_path / "plan.txt"
    planned.write_text("\ufeff" + nodeids[0] + "\n", encoding="utf-8")
    assert read_plan(planned) == nodeids


def test_a_plan_that_is_not_utf8_fails_closed(tmp_path: Path) -> None:
    planned = tmp_path / "plan.txt"
    planned.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SuiteDidNotRun, match="not a readable plan"):
        read_plan(planned)
