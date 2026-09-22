"""Runner partitions preserve selected behavior, including with xdist."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

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
