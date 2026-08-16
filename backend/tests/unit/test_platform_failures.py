import errno

from app.domain.agent.platform_failures import (
    ALL_FAILURES,
    HOST_SCOPED_CODES,
    PROMPT_UNDELIVERED,
    PROMPT_UNDELIVERED_CODE,
    RUNTIME_IMAGE_MISSING_CODE,
    STORAGE_EXHAUSTED_CODE,
    TURN_TIMEOUT,
    TURN_TIMEOUT_CODE,
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
        # 平台提示统一契约: 卡面留一句，解释性的几句进 detail 由前端折叠。
        "detail": (
            "项目文件和已完成的改动都还在。平台正在清理临时空间，"
            "请稍后再 @芝士 继续；若持续出现，请联系管理员。"
        ),
        "detail_label": "详细说明",
    }
    # 卡面是一句话；那句解释没丢，它在展开区里。
    assert failure.content.count("。") == 1
    assert "项目文件和已完成的改动都还在" in failure.detail
    # 脱敏在两处都要成立 —— 把长文挪进 detail 不是把它挪出审查范围。
    assert "/home/private" not in failure.content
    assert "/home/private" not in failure.detail


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
        "detail": (
            "本轮还没有开始执行，项目文件没有受到影响。"
            "请稍后再 @芝士 重试；若持续出现，请联系管理员。"
        ),
        "detail_label": "详细说明",
    }
    assert failure.content.count("。") == 1
    assert "本轮还没有开始执行" in failure.detail
    assert "pull access denied" not in failure.content
    assert "pull access denied" not in failure.detail


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
        "detail": (
            "那个文件的属主不是平台进程，平台读不到它，话题就起不来。"
            "项目文件和已提交的改动都没有受影响，版本历史也没有动过。"
            "平台会在下一次访问时自动清掉这个文件并恢复，"
            "请稍后再 @芝士 重试；若反复出现，请把这条提示转给管理员。"
        ),
        "detail_label": "详细说明",
    }
    assert failure.content.count("。") == 1
    assert "版本历史也没有动过" in failure.detail
    # No internal paths, and above all no "AI 服务" — that misdirection is the
    # reason this classification exists. Both halves are user-facing now, so
    # both are checked.
    assert "/ws/p" not in failure.content + failure.detail
    assert "AI 服务" not in failure.content + failure.detail


def test_undelivered_prompt_is_not_blamed_on_the_ai_service():
    """The hooks substrate's delivery timeout: the message never reached the
    claude session, so the model provider never saw this turn at all. It used to
    fall through to chat.py's `else` and render as 「AI 服务返回错误」."""
    from app.domain.agent.hooks_substrate import UNDELIVERED_MESSAGE

    failure = classify_platform_failure(UNDELIVERED_MESSAGE)

    assert failure is not None
    assert failure.code == PROMPT_UNDELIVERED_CODE
    assert failure.meta["event_type"] == "platform_error"
    assert failure.meta["title"] == "消息没送到芝士那边"
    # Re-@ing does work (a new session is opened), so this is retryable.
    assert failure.retryable is True
    # A dead screen is this topic's problem, not the box's — see the comment on
    # PROMPT_UNDELIVERED. Indicting the machine would quarantine a healthy box.
    assert failure.host_scoped is False
    assert failure.code not in HOST_SCOPED_CODES
    # 卡面一句话，解释在展开区（平台提示统一契约）。
    assert failure.content.count("。") == 1
    assert "不是 AI 服务的问题" in failure.detail
    assert "再 @ 一次" in failure.detail


def test_turn_timeout_is_classified_for_every_hooks_backend():
    """All three timeout messages — the base one and each transport's prefixed
    variant — must classify, or the backend that wrote its own copy silently
    keeps blaming the AI service."""
    from app.domain.agent.device_provider import DeviceProvider
    from app.domain.agent.hooks_substrate import HooksTurnProvider
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    for message in (
        HooksTurnProvider._timeout_message,
        TmuxHooksProvider._timeout_message,
        DeviceProvider._timeout_message,
    ):
        failure = classify_platform_failure(message)
        assert failure is not None, message
        assert failure.code == TURN_TIMEOUT_CODE, message

    assert TURN_TIMEOUT.retryable is True
    # Turns time out on perfectly healthy machines; this must never quarantine one.
    assert TURN_TIMEOUT.host_scoped is False
    assert TURN_TIMEOUT_CODE not in HOST_SCOPED_CODES
    assert TURN_TIMEOUT.meta["title"] == "这轮跑到时间上限，被强制结束"
    assert TURN_TIMEOUT.content.count("。") == 1
    assert "不是 AI 服务返回的错误" in TURN_TIMEOUT.detail


def test_a_more_specific_cause_still_wins_over_the_timeout_marker():
    """A turn that timed out BECAUSE the disk filled must report the disk — the
    symptom must never mask the cause it is checked after."""
    failure = classify_platform_failure(
        "tmux 轮次超时（[Errno 28] No space left on device）"
    )
    assert failure is not None
    assert failure.code == STORAGE_EXHAUSTED_CODE


def test_the_two_new_markers_do_not_fire_on_unrelated_copy():
    assert classify_platform_failure("请求超时，请稍后再试") is None
    assert classify_platform_failure("Read timed out. (read timeout=600)") is None
    assert classify_platform_failure("消息发送失败") is None


def test_every_failure_is_registered_for_host_scoped_accounting():
    """`HOST_SCOPED_CODES` is derived from `ALL_FAILURES`, so a failure left out
    of the tuple silently opts itself out of the machine-health accounting."""
    for failure in (PROMPT_UNDELIVERED, TURN_TIMEOUT):
        assert failure in ALL_FAILURES
