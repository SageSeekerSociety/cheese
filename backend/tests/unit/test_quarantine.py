"""Every line of tests/quarantine.txt names a test that still exists and the
issue that owns its fix."""

from pathlib import Path

import pytest

from tests.conftest import _quarantine

BACKEND = Path(__file__).resolve().parents[2]


def test_every_quarantined_test_still_exists() -> None:
    for nodeid in _quarantine():
        path, *names = nodeid.split("::")
        source = BACKEND / path
        assert source.is_file(), f"{nodeid}: {path} is gone, so remove its line"
        name = names[-1].split("[")[0]
        assert f"def {name}(" in source.read_text(), (
            f"{nodeid}: {path} no longer defines {name}, so remove its line"
        )


def test_a_line_without_an_issue_aborts_collection(tmp_path, monkeypatch) -> None:
    listing = tmp_path / "quarantine.txt"
    listing.write_text("tests/unit/test_a.py::test_one  # flaky, will fix\n")
    monkeypatch.setattr("tests.conftest._QUARANTINE", listing)
    with pytest.raises(pytest.UsageError, match="GitHub issue link"):
        _quarantine()
    listing.write_text(
        "tests/unit/test_a.py::test_one  # https://github.com/o/r/issues/7 flaky\n"
    )
    assert _quarantine() == {
        "tests/unit/test_a.py::test_one": "https://github.com/o/r/issues/7"
    }
