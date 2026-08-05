import errno

from app.domain.agent.platform_failures import (
    STORAGE_EXHAUSTED_CODE,
    classify_platform_failure,
    is_storage_exhausted,
)


def test_storage_exhaustion_matches_errno_and_provider_text():
    assert is_storage_exhausted(OSError(errno.ENOSPC, "No space left on device"))
    assert is_storage_exhausted(
        "tmux 后端启动失败：[Errno 28] No space left on device: '/work/SKILL.md'"
    )
    assert is_storage_exhausted("screen setup failed: ENOSPC")


def test_storage_exhaustion_walks_exception_chain():
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as exc:
            raise RuntimeError("tmux startup failed") from exc
    except RuntimeError as wrapped:
        assert is_storage_exhausted(wrapped)


def test_storage_exhaustion_does_not_guess_from_vague_space_copy():
    assert not is_storage_exhausted("There is no space in the schedule")
    assert classify_platform_failure(RuntimeError("docker unavailable")) is None


def test_storage_failure_payload_is_stable_and_sanitized():
    failure = classify_platform_failure(
        "[Errno 28] No space left on device: '/home/private/worktree'"
    )
    assert failure is not None
    assert failure.code == STORAGE_EXHAUSTED_CODE
    assert failure.meta == {
        "event_type": "platform_error",
        "code": "storage_exhausted",
        "severity": "error",
        "title": "运行环境存储空间不足",
        "retryable": True,
    }
    assert "项目文件和已完成的改动都还在" in failure.content
    assert "/home/private" not in failure.content
