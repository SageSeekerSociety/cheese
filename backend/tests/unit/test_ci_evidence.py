"""A worker that pytest-timeout ends leaves the case and its stacks behind, and
its retry keeps the run green without hiding that it needed one."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.assert_suite_ran import SuiteDidNotRun, assert_suite_ran, not_clean

BACKEND = Path(__file__).resolve().parents[2]

SAMPLE = """
import pathlib, time

def test_steady():
    pass

def test_wedges_once():
    mark = pathlib.Path(__file__).with_name("wedged")
    if not mark.exists():
        mark.write_text("")
        time.sleep(30)
"""


@pytest.fixture(scope="module")
def wedged_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp_path = tmp_path_factory.mktemp("wedged")
    (tmp_path / "test_sample.py").write_text(SAMPLE)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    evidence = tmp_path / "evidence"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            # ci_shard writes the cheese_nodeid each JUnit case carries in CI.
            "-p",
            "scripts.ci_shard",
            "-p",
            "scripts.ci_evidence",
            "--ci-evidence-dir",
            str(evidence),
            "-n",
            "1",
            "--timeout",
            "1",
            "-o",
            "timeout_method=thread",
            "--reruns",
            "1",
            "--junitxml",
            str(evidence / "results.xml"),
            "-q",
            "-p",
            "no:cacheprovider",
            str(tmp_path / "test_sample.py"),
        ],
        cwd=tmp_path,
        # Installed plugins load the way CI loads them: rerunfailures only
        # retries a crashed case when xdist is registered under its own name.
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "node down" in result.stdout, result.stdout
    return evidence


def test_the_lost_worker_names_its_case_and_where_it_was(wedged_run: Path) -> None:
    logs = {path.name: path.read_text() for path in wedged_run.glob("hang-*.log")}
    lost = [text for text in logs.values() if "Timeout (" in text]
    assert len(lost) == 1, logs
    started = [line for line in lost[0].splitlines() if " start " in line]
    assert started[-1].split()[2] == "test_sample.py::test_wedges_once"
    assert "end test_sample.py::test_wedges_once" not in lost[0]
    # The dump names the line the case was stuck on, not just the case.
    assert 'test_sample.py", line 11 in test_wedges_once' in lost[0]


def test_the_retry_is_recorded_and_named_on_the_run(wedged_run: Path) -> None:
    reruns = [
        json.loads(line)
        for line in (wedged_run / "reruns.jsonl").read_text().splitlines()
    ]
    assert [(r["nodeid"], r["crashed"]) for r in reruns] == [
        ("test_sample.py::test_wedges_once", True)
    ]
    results = wedged_run / "results.xml"
    # Without the record, the crash's nameless testcase is unexplained.
    with pytest.raises(SuiteDidNotRun, match="nameless"):
        assert_suite_ran(results, at_least=2)
    assert (
        assert_suite_ran(results, at_least=2, reruns=wedged_run / "reruns.jsonl") == 2
    )
    [line] = not_clean(results, wedged_run / "reruns.jsonl")
    assert "test_wedges_once passed only on a retry; first its worker died" in line


@pytest.mark.parametrize("method", ["signal", "thread"])
def test_a_retry_gets_a_timeout_of_its_own(tmp_path: Path, method: str) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(
        "import pathlib, time\n"
        "def test_fails_then_wedges():\n"
        "    mark = pathlib.Path(__file__).with_name('failed')\n"
        "    if not mark.exists():\n"
        "        mark.write_text('')\n"
        "        assert False\n"
        "    time.sleep(30)\n"
    )
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci_evidence",
            "--ci-evidence-dir",
            str(tmp_path / "evidence"),
            "--timeout",
            "1",
            "-o",
            f"timeout_method={method}",
            "--reruns",
            "1",
            "-q",
            str(tmp_path / "test_sample.py"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert time.monotonic() - started < 10
    assert "Timeout" in result.stdout + result.stderr
    log = (tmp_path / "evidence" / "hang-main.log").read_text()
    assert log.count("start test_sample.py::test_fails_then_wedges") == 2
    assert "test_fails_then_wedges" in log.split("Timeout (")[-1]


@pytest.mark.parametrize("workers", [0, 1])
def test_a_teardown_retry_counts_once_and_warns(tmp_path: Path, workers: int) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(
        "import pathlib, pytest\n"
        "@pytest.fixture\n"
        "def teardown_once():\n"
        "    yield\n"
        "    mark = pathlib.Path(__file__).with_name('torn_down')\n"
        "    if not mark.exists():\n"
        "        mark.write_text('')\n"
        "        raise RuntimeError('teardown once')\n"
        "def test_retried(teardown_once): pass\n"
        "def test_steady(): pass\n"
    )
    evidence = tmp_path / "evidence"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci_shard",
            "-p",
            "scripts.ci_evidence",
            "--ci-evidence-dir",
            str(evidence),
            "--ci-selection-output",
            str(evidence),
            "-n",
            str(workers),
            "--reruns",
            "1",
            "--junitxml",
            str(evidence / "results.xml"),
            "-q",
            str(tmp_path / "test_sample.py"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    results, reruns = evidence / "results.xml", evidence / "reruns.jsonl"
    assert (
        assert_suite_ran(results, at_least=2, selection_dir=evidence, reruns=reruns)
        == 2
    )
    [warning] = not_clean(results, reruns)
    assert "test_retried passed only on a retry" in warning
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(results, at_least=2, selection_dir=evidence)
