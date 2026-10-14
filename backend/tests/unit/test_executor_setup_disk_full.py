"""An executor that could not be installed because the machine's disk is full
says so, rather than repeating the path the machine's error named."""

from app.domain.machine.session_work import ExecutorSetupFailed


def test_a_full_disk_is_named_as_such():
    failed = ExecutorSetupFailed(
        "Traceback (most recent call last):\n"
        "OSError: [Errno 28] No space left on device: '/home/x/.cheese/home/p/r'"
    )

    reason = failed.reason()

    assert "磁盘满了" in reason
    assert "/home/x" not in reason


def test_any_other_failure_keeps_the_machines_own_words():
    failed = ExecutorSetupFailed("installing\nPermissionError: denied")

    assert failed.reason() == "PermissionError: denied"
