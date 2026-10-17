"""Runner partitions preserve selected behavior, including with xdist."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def run_shard(tmp_path, shard, *, workers=0):
    output = tmp_path / f"reports-{shard.replace('/', '-')}-{workers}"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.ci_shard",
        "--ci-shard",
        shard,
        "--ci-selection-output",
        str(output),
        "--junitxml",
        str(output / "results.xml"),
        "-m",
        "chosen",
        "-k",
        "not excluded",
        "-q",
        str(tmp_path / "test_sample.py"),
    ]
    if workers:
        command += ["-p", "xdist.plugin", "-n", str(workers)]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(BACKEND),
        },
    )
    return result, [json.loads(p.read_text()) for p in sorted(output.glob("*.json"))]


@pytest.fixture
def selected_cases(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = chosen\n")
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "@pytest.mark.chosen\n"
        "@pytest.mark.parametrize('value', range(40))\n"
        "def test_value(value): assert value >= 0\n"
        "@pytest.mark.chosen\n"
        "def test_excluded(): assert False\n"
        "def test_other(): assert False\n"
    )
    return {f"test_sample.py::test_value[{i}]" for i in range(40)}


def test_all_selected_cases_run_once_across_runners(tmp_path, selected_cases):
    seen = set()
    for index in range(4):
        result, manifests = run_shard(tmp_path, f"{index}/4")
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(manifests) == 1
        assert set(manifests[0]["selected"]) == selected_cases
        assigned = set(manifests[0]["assigned"])
        assert assigned
        assert not seen & assigned
        seen.update(assigned)
    assert seen == selected_cases


def test_xdist_workers_agree_with_serial_partition(tmp_path, selected_cases):
    serial, serial_manifests = run_shard(tmp_path, "0/4")
    parallel, parallel_manifests = run_shard(tmp_path, "0/4", workers=2)
    assert serial.returncode == parallel.returncode == 0, (
        parallel.stdout + parallel.stderr
    )
    assert len(parallel_manifests) == 2
    assert all(m == serial_manifests[0] for m in parallel_manifests)
    for workers in (0, 2):
        report = ET.parse(tmp_path / f"reports-0-4-{workers}/results.xml")
        identities = [
            prop.get("value")
            for prop in report.findall(
                ".//testcase/properties/property[@name='cheese_nodeid']"
            )
        ]
        assert len(identities) == len(set(identities))
        assert set(identities) == set(serial_manifests[0]["assigned"])


@pytest.mark.parametrize("shard", ["0/0", "-1/4", "4/4", "broken", "1/2/3"])
def test_invalid_partition_fails_before_running(tmp_path, selected_cases, shard):
    result, _ = run_shard(tmp_path, shard)
    assert result.returncode == 4
    assert "--ci-shard" in result.stderr


def write_duration_report(directory, layer, shard, cases):
    report = directory / f"backend-{layer}-{shard}" / "results.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite")
    for nodeid, seconds in cases:
        case = ET.SubElement(suite, "testcase", time=str(seconds))
        if nodeid is not None:
            properties = ET.SubElement(case, "properties")
            ET.SubElement(properties, "property", name="cheese_nodeid", value=nodeid)
    ET.ElementTree(suite).write(report)


@pytest.fixture
def duration_runs(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_duration_report(
        first,
        "pure",
        0,
        [
            ("tests/test_a.py::test_fast", 2),
            ("tests/test_a.py::TestGroup::test_slow[x.y::z]", 10),
            (None, 999),
        ],
    )
    write_duration_report(
        first,
        "pure",
        1,
        [
            ("tests/test_a.py::test_fast", 3),
            ("tests/test_b.py::test_only_first", 7),
        ],
    )
    write_duration_report(
        second,
        "pure",
        0,
        [
            ("tests/test_a.py::test_fast", 9),
            ("tests/test_a.py::TestGroup::test_slow[x.y::z]", 4),
            ("tests/test_a.py::TestGroup::test_slow[other]", 1),
        ],
    )
    write_duration_report(first, "contract", 0, [("tests/test_c.py::test_api", 11)])
    write_duration_report(first, "integration", 0, [("tests/test_d.py::test_db", 13)])
    write_duration_report(
        first, "unknown", 0, [("tests/test_ignored.py::test_it", 1000)]
    )
    return [str(first), str(second)]


def assert_case_durations(report):
    assert report["tests"] == {
        "tests/test_a.py::TestGroup::test_slow[other]": 1.0,
        "tests/test_a.py::TestGroup::test_slow[x.y::z]": 7.0,
        "tests/test_a.py::test_fast": 7.0,
        "tests/test_b.py::test_only_first": 7.0,
        "tests/test_c.py::test_api": 11.0,
        "tests/test_d.py::test_db": 13.0,
    }


def test_duration_generation_retains_full_nodeids(
    tmp_path, monkeypatch, capsys, duration_runs
):
    from scripts import test_durations

    output = tmp_path / "durations.json"
    monkeypatch.setattr(test_durations, "OUTPUT", output)
    slow_list = tmp_path / "slow_tests.txt"
    slow_list.write_text("tests/test_c.py::test_api\n")
    monkeypatch.setattr(test_durations, "SLOW_LIST", slow_list)
    assert test_durations.main(duration_runs) == 0
    stdout = capsys.readouterr().out
    assert "tests/slow_tests.txt" in stdout
    assert "tests/test_c.py::test_api" in stdout
    report = json.loads(output.read_text())
    assert_case_durations(report)
    assert list(report["tests"]) == sorted(report["tests"])
    assert {k: v for k, v in report.items() if k != "tests"} == {
        "pure": {"tests/test_a.py": [14.5, 3], "tests/test_b.py": [7.0, 1]},
        "contract": {"tests/test_c.py": [11.0, 1]},
        "integration": {"tests/test_d.py": [13.0, 1]},
    }
    original = output.read_text()
    assert test_durations.main(list(reversed(duration_runs))) == 0
    assert output.read_text() == original


def test_nodeid_guard_rejects_file_only_durations(tmp_path, monkeypatch, duration_runs):
    from scripts import test_durations

    output = tmp_path / "durations.json"
    monkeypatch.setattr(test_durations, "OUTPUT", output)
    original = test_durations._nodeid
    # Deliberately lose function/class/parameter identity before aggregation.
    monkeypatch.setattr(
        test_durations,
        "_nodeid",
        lambda case: nodeid.split("::")[0] if (nodeid := original(case)) else None,
    )
    assert test_durations.main(duration_runs) == 0
    with pytest.raises(AssertionError):
        assert_case_durations(json.loads(output.read_text()))


@pytest.mark.parametrize("limit", [1, 3, 20])
def test_top_tests_orders_full_nodeids(
    tmp_path, monkeypatch, capsys, duration_runs, limit
):
    from scripts import ci_shard, test_durations

    output = tmp_path / "durations.json"
    monkeypatch.setattr(test_durations, "OUTPUT", output)
    monkeypatch.setattr(ci_shard, "DURATIONS", output)
    assert test_durations.main(duration_runs) == 0
    capsys.readouterr()
    recorded = json.loads(output.read_text())
    recorded["tests"] = dict(reversed(list(recorded["tests"].items())))
    output.write_text(json.dumps(recorded))
    assert ci_shard.main(["--top-tests", str(limit)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert (
        captured.out.splitlines()
        == [
            "tests/test_d.py::test_db\t13.00",
            "tests/test_c.py::test_api\t11.00",
            "tests/test_a.py::TestGroup::test_slow[x.y::z]\t7.00",
            "tests/test_a.py::test_fast\t7.00",
            "tests/test_b.py::test_only_first\t7.00",
            "tests/test_a.py::TestGroup::test_slow[other]\t1.00",
        ][:limit]
    )


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_top_tests_rejects_invalid_counts(value, capsys):
    from scripts import ci_shard

    with pytest.raises(SystemExit) as exc:
        ci_shard.main(["--top-tests", value])
    assert exc.value.code == 2
    assert "--top-tests" in capsys.readouterr().err


@pytest.mark.parametrize(
    "recorded", [None, {"pure": {"tests/test_a.py": [3, 1]}}, {"tests": {}}]
)
def test_top_tests_reports_missing_test_measurements(
    tmp_path, monkeypatch, capsys, recorded
):
    from scripts import ci_shard

    output = tmp_path / "durations.json"
    if recorded is not None:
        output.write_text(json.dumps(recorded))
    monkeypatch.setattr(ci_shard, "DURATIONS", output)
    assert ci_shard.main(["--top-tests", "3"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no test durations" in captured.err


def test_shard_script_without_query_remains_silent(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "ci_shard.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_test_measurements_do_not_change_module_order(tmp_path, monkeypatch):
    from scripts import ci_shard

    output = tmp_path / "durations.json"
    monkeypatch.setattr(ci_shard, "DURATIONS", output)
    recorded = {"pure": {"tests/test_fast.py": [4, 2], "tests/test_slow.py": [18, 3]}}
    nodeids = [
        "tests/test_fast.py::test_one",
        "tests/test_slow.py::test_one",
        "tests/test_unknown.py::test_one",
        "tests/test_fast.py::test_two",
    ]
    items = [
        SimpleNamespace(
            nodeid=nodeid,
            iter_markers=lambda: [SimpleNamespace(name="pure")],
        )
        for nodeid in nodeids
    ]
    for tests in (None, {nodeids[0]: 9999, nodeids[1]: 0.01}):
        if tests is not None:
            recorded["tests"] = tests
        output.write_text(json.dumps(recorded))
        assert [item.nodeid for item in ci_shard._longest_module_first(items)] == [
            nodeids[1],
            nodeids[0],
            nodeids[3],
            nodeids[2],
        ]


def test_duration_generation_without_samples_preserves_output(
    tmp_path, monkeypatch, capsys
):
    from scripts import test_durations

    output = tmp_path / "durations.json"
    output.write_text("existing measurements\n")
    monkeypatch.setattr(test_durations, "OUTPUT", output)
    assert test_durations.main([str(tmp_path / "empty")]) == 1
    assert output.read_text() == "existing measurements\n"
    assert "cheese_nodeid" in capsys.readouterr().err
