import os

import pytest

from app.domain.agent.harness.claude_code.remote_execution.context_service import (
    address,
    call,
    serve,
)


def test_context_service_reads_current_state_and_cleans_up(tmp_path):
    target = tmp_path / "execution.json"
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.write_text("first")
    assert call(target) is False
    with serve(target, lambda: output.write_text(source.read_text())):
        assert os.stat(address(target)).st_mode & 0o777 == 0o600
        assert call(target)
        assert output.read_text() == "first"
        source.write_text("second")
        assert call(target)
        assert output.read_text() == "second"
    assert not os.path.exists(address(target))
    assert call(target) is False


def test_context_failure_reaches_caller_and_service_survives(tmp_path):
    target = tmp_path / "execution.json"
    attempts = []

    def synchronize():
        attempts.append(True)
        if len(attempts) == 1:
            raise ValueError("context unavailable")

    with serve(target, synchronize):
        with pytest.raises(RuntimeError, match="context unavailable"):
            call(target)
        assert call(target)
    assert len(attempts) == 2


def test_distinct_sessions_do_not_share_context_service(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    calls = []
    with serve(left, lambda: calls.append("left")):
        assert call(right) is False
        with serve(right, lambda: calls.append("right")):
            assert call(right)
            assert call(left)
    assert calls == ["right", "left"]
