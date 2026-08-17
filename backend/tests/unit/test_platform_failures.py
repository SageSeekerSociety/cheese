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


def test_prompt_undelivered_is_not_blamed_on_the_ai_service():
    """The hooks substrate raises this itself when nothing came back from the
    claude session. It used to fall through to chat.py's `else` and render as
    「AI 服务返回错误」 — blaming the provider for a turn it never saw, which
    sends whoever is debugging in exactly the wrong direction."""
    from app.domain.agent.platform_failures import (
        PROMPT_UNDELIVERED,
        PROMPT_UNDELIVERED_CODE,
        PROMPT_UNDELIVERED_MESSAGE,
        classify_platform_failure,
    )

    failure = classify_platform_failure(PROMPT_UNDELIVERED_MESSAGE)
    assert failure is not None
    assert failure.code == PROMPT_UNDELIVERED_CODE
    assert failure is PROMPT_UNDELIVERED
    assert "AI 服务" not in failure.content
    assert failure.retryable is True
    # A wedged session belongs to THIS topic's screen, not to the box — counting
    # it against the machine would quarantine a healthy host and drag unrelated
    # topics off it.
    assert failure.host_scoped is False


def test_turn_timeout_is_recognised_behind_each_transport_prefix():
    """Both hooks backends prefix their transport onto the sentence, so the
    classifier has to match the shared tail. A backend that renamed its message
    and lost the marker would silently go back to 「AI 服务返回错误」."""
    from app.domain.agent.platform_failures import (
        TURN_TIMEOUT,
        TURN_TIMEOUT_MARKER,
        classify_platform_failure,
    )

    for message in (
        TURN_TIMEOUT_MARKER,
        f"tmux {TURN_TIMEOUT_MARKER}",
        f"device {TURN_TIMEOUT_MARKER}",
    ):
        assert classify_platform_failure(message) is TURN_TIMEOUT, message
    assert "AI 服务" not in TURN_TIMEOUT.content
    assert TURN_TIMEOUT.host_scoped is False


def test_every_hooks_backend_keeps_the_timeout_marker():
    """The wiring, not the copy: if a subclass hardcodes its own sentence again
    the classification is lost, and nothing else in the suite would notice."""
    from app.domain.agent.device_provider import DeviceProvider
    from app.domain.agent.hooks_substrate import HooksSessionProvider
    from app.domain.agent.platform_failures import TURN_TIMEOUT_MARKER
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    for provider in (HooksSessionProvider, TmuxHooksProvider, DeviceProvider):
        assert TURN_TIMEOUT_MARKER in provider._timeout_message, provider.__name__


def test_undelivered_message_is_the_classified_one():
    """The session monitor's delivery failure must match the classifier."""
    from app.domain.agent.hooks_substrate import UNDELIVERED_MESSAGE
    from app.domain.agent.platform_failures import (
        PROMPT_UNDELIVERED,
        classify_platform_failure,
    )

    assert classify_platform_failure(UNDELIVERED_MESSAGE) is PROMPT_UNDELIVERED
