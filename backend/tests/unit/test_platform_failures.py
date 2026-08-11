import errno

from app.domain.agent.platform_failures import (
    RUNTIME_IMAGE_MISSING_CODE,
    STORAGE_EXHAUSTED_CODE,
    WORKSPACE_VCS_PERMS_CODE,
    classify_platform_failure,
    is_storage_exhausted,
    is_workspace_vcs_perms,
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


def test_missing_runtime_image_is_a_sanitized_platform_event():
    failure = classify_platform_failure(
        "tmux container create failed: Unable to find image "
        "'cheesex-agent-tmux:latest' locally: pull access denied"
    )

    assert failure is not None
    assert failure.code == RUNTIME_IMAGE_MISSING_CODE
    assert failure.meta == {
        "event_type": "platform_error",
        "code": "runtime_image_missing",
        "severity": "error",
        "title": "Agent 运行组件暂时缺失",
        "retryable": True,
    }
    assert "本轮还没有开始执行" in failure.content
    assert "pull access denied" not in failure.content


def test_workspace_vcs_perms_matches_jj_and_backend_wording():
    """Both ends of the same failure: jj's own English, and the sentence the
    backend rewrites it into before it leaves workspace/service.py."""
    assert is_workspace_vcs_perms(
        "jj workspace failed: Internal error: Failed to determine the secure "
        "config for a repo"
    )
    assert is_workspace_vcs_perms(
        "工作区版本库权限异常：/ws/x/.jj/repo/config-id 的属主…"
    )


def test_workspace_vcs_perms_walks_exception_chain():
    """Production wraps it twice (ValidationError → ScreenSetupError)."""
    try:
        try:
            raise RuntimeError(
                "Internal error: Failed to determine the secure config for a repo"
            )
        except RuntimeError as exc:
            raise RuntimeError("tmux 后端启动失败") from exc
    except RuntimeError as wrapped:
        assert is_workspace_vcs_perms(wrapped)


def test_workspace_vcs_perms_does_not_guess_from_any_permission_error():
    """Plenty of unrelated failures say "Permission denied" — only jj's
    secure-config wording means the store is owned by another uid."""
    assert not is_workspace_vcs_perms("git push failed: Permission denied (publickey)")
    assert classify_platform_failure("PermissionError: [Errno 13] '/tmp/x'") is None


def test_workspace_vcs_perms_payload_is_stable_and_sanitized():
    failure = classify_platform_failure(
        "tmux 后端启动失败：工作区版本库权限异常：/ws/p/.jj/repo/config-id 的属主不是"
        "后端进程。原始报错：Internal error: Failed to determine the secure config "
        "for a repo"
    )

    assert failure is not None
    assert failure.code == WORKSPACE_VCS_PERMS_CODE
    assert failure.meta == {
        "event_type": "platform_error",
        "code": "workspace_vcs_perms",
        "severity": "error",
        "title": "工作区版本库权限异常",
        "retryable": True,
    }
    assert "版本历史也没有动过" in failure.content
    # No internal paths, and above all no "AI 服务" — that misdirection is the
    # reason this classification exists.
    assert "/ws/p" not in failure.content
    assert "AI 服务" not in failure.content
