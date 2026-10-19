"""What scripts/test_durations.py reads back out of a run's artifacts.

The module table it writes is what the shards are ordered by, and the table it
prints is what tests/slow_tests.txt is written from: both are read off
results.xml files whose shape is fixed by the CI run that wrote them, so a
hand-built one here is enough to say what the reader makes of them.
"""

from pathlib import Path

import pytest

from scripts import test_durations
from tests.conftest import _LAYER_CEILING_S, _ceiling_scale


def _artifacts(root: Path, layer: str, entries: list[tuple[str, float]]) -> Path:
    """One run's downloaded artifacts for one layer: ``backend-<layer>-0-1``."""
    cases = "".join(
        f'<testcase name="{nodeid.split("::")[-1]}" time="{seconds}">'
        f'<properties><property name="cheese_nodeid" value="{nodeid}"/></properties>'
        "</testcase>"
        for nodeid, seconds in entries
    )
    directory = root / f"backend-{layer}-0-1"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.xml").write_text(
        f"<testsuites><testsuite>{cases}</testsuite></testsuites>"
    )
    return directory


def _line(report: str, nodeid: str) -> str:
    return next(line for line in report.splitlines() if nodeid in line)


def _run(times: dict[str, float], layer: str = "pure") -> test_durations.Run:
    return test_durations.Run(modules={}, tests={layer: times})


def test_a_run_reads_back_by_module_and_by_test(tmp_path: Path) -> None:
    run = test_durations.read_run(
        _artifacts(
            tmp_path,
            "pure",
            [
                ("tests/unit/test_a.py::test_first", 2.0),
                ("tests/unit/test_a.py::test_second", 3.0),
                ("tests/unit/test_b.py::test_only", 4.0),
            ],
        )
    )
    assert run.modules == {
        "pure": {"tests/unit/test_a.py": (5.0, 2), "tests/unit/test_b.py": (4.0, 1)}
    }
    assert run.tests == {
        "pure": {
            "tests/unit/test_a.py::test_first": 2.0,
            "tests/unit/test_a.py::test_second": 3.0,
            "tests/unit/test_b.py::test_only": 4.0,
        }
    }


def test_a_nodeid_reported_twice_is_summed_in_both_tables(
    tmp_path: Path,
) -> None:
    """Every execution took time, so both tables add it up; pytest sends a rerun
    as one testcase that already holds its attempts, which this leaves alone."""
    run = test_durations.read_run(
        _artifacts(
            tmp_path,
            "pure",
            [
                ("tests/unit/test_a.py::test_flaky", 2.0),
                ("tests/unit/test_a.py::test_flaky", 3.0),
            ],
        )
    )
    assert run.tests == {"pure": {"tests/unit/test_a.py::test_flaky": 5.0}}
    assert run.modules == {"pure": {"tests/unit/test_a.py": (5.0, 2)}}


def test_each_layer_keeps_its_own_artifacts(tmp_path: Path) -> None:
    _artifacts(tmp_path, "pure", [("tests/unit/test_a.py::test_a", 1.0)])
    contract = _artifacts(
        tmp_path, "contract", [("tests/contract/test_b.py::test_b", 2.0)]
    )
    run = test_durations.read_run(tmp_path)
    assert run.tests == {
        "pure": {"tests/unit/test_a.py::test_a": 1.0},
        "contract": {"tests/contract/test_b.py::test_b": 2.0},
    }
    # A directory that is not a layer's artifacts is not guessed at either.
    assert test_durations.read_run(contract.parent / "somewhere-else") == (
        test_durations.Run(modules={}, tests={})
    )


def test_the_ceiling_reader_agrees_with_the_conftest_that_owns_it() -> None:
    """The report says how close to the ceiling a test came; conftest owns it."""
    assert test_durations._ceilings() == _LAYER_CEILING_S


def test_the_layer_ceiling_scale_comes_from_the_environment(monkeypatch) -> None:
    """Only the fast tier sets it; full CI keeps the strict numbers.

    The ceilings were measured on a machine the run had to itself. The tier
    that runs two trees at once tells this run how much wall clock it lost, so
    a test is not failed for the sharing; anything but a finite number >= 1 is
    a usage error rather than a silently different allowance.
    """
    monkeypatch.delenv("CHEESE_TEST_CEILING_SCALE", raising=False)
    assert _ceiling_scale() == 1
    monkeypatch.setenv("CHEESE_TEST_CEILING_SCALE", "1.75")
    assert _ceiling_scale() == 1.75
    for raw in ("0", "-1", "nan", "inf", "x", ""):
        monkeypatch.setenv("CHEESE_TEST_CEILING_SCALE", raw)
        with pytest.raises(pytest.UsageError):
            _ceiling_scale()


def test_the_scale_reaches_the_item_not_only_the_parser(monkeypatch) -> None:
    """The parsed factor must be applied at collection, not just accepted.

    Without this the multiplication can be deleted and every test still runs
    on the strict single-process ceiling — the exact loss the scale exists to
    prevent, with a green self-test.
    """
    from tests import conftest

    class Item:
        nodeid = "tests/unit/test_x.py::test_a"
        path = Path("tests/unit/test_x.py")
        fixturenames = ()

        def __init__(self):
            self.stash = {}
            self.markers = []

        def iter_markers(self):
            return iter(())

        def add_marker(self, marker):
            self.markers.append(marker)

        def get_closest_marker(self, name):
            return None

    monkeypatch.setattr(conftest, "_slow_baseline", lambda: frozenset())
    monkeypatch.setattr(conftest, "_quarantine", lambda: {})
    monkeypatch.setattr(conftest, "_layer_of", lambda item: "pure")
    monkeypatch.setattr(conftest, "measures_query_counts", lambda path: False)
    monkeypatch.setenv("CHEESE_TEST_CEILING_SCALE", "2")
    item = Item()
    conftest.pytest_collection_modifyitems([item])
    assert item.stash[conftest._CEILING] == ("pure", 20, 2)


def test_the_over_ceiling_text_names_the_effective_allowance() -> None:
    from tests.conftest import _over_ceiling
    scaled = _over_ceiling("pure", 25.0, 20.0, 2)
    assert "25.0 s with setup" in scaled
    assert "(10 s x 2 for CHEESE_TEST_CEILING_SCALE)" in scaled
    strict = _over_ceiling("pure", 25.0, 10.0, 1)
    assert "10 s" in strict
    assert "CHEESE_TEST_CEILING_SCALE" not in strict


def test_the_report_runs_slowest_first_and_flags_what_a_fix_has_reached() -> None:
    first, second, *never = test_durations.listed_slow_tests()
    report = test_durations.slow_report([_run({first: 2.0, second: 8.0})])
    assert report.index(second) < report.index(first), report
    assert "under the 60% line" in _line(report, first), report
    assert "under the 60% line" not in _line(report, second), report
    assert f"not seen in any of these runs: {never[0]}" in report, report
