"""What scripts/test_durations.py reads back out of a run's artifacts.

The module table it writes is what the shards are ordered by, and the table it
prints is what tests/slow_tests.txt is written from: both are read off
results.xml files whose shape is fixed by the CI run that wrote them, so a
hand-built one here is enough to say what the reader makes of them.
"""

from pathlib import Path

from scripts import test_durations
from tests.conftest import _LAYER_CEILING_S


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


def test_a_rerun_counts_once_for_the_test_and_twice_for_the_module(
    tmp_path: Path,
) -> None:
    """The slow list asks how long a test takes, not how many attempts it took."""
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


def test_the_report_runs_slowest_first_and_flags_what_a_fix_has_reached() -> None:
    first, second, *never = test_durations.listed_slow_tests()
    report = test_durations.slow_report([_run({first: 2.0, second: 8.0})])
    assert report.index(second) < report.index(first), report
    assert "under the 60% line" in _line(report, first), report
    assert "under the 60% line" not in _line(report, second), report
    assert f"not seen in any of these runs: {never[0]}" in report, report
