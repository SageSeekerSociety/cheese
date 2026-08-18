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
        "title": "运行环境镜像暂时不可用",
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
        classify_platform_failure,
    )

    failure = classify_platform_failure("", code=PROMPT_UNDELIVERED_CODE)
    assert failure is PROMPT_UNDELIVERED
    assert "AI 服务" not in failure.content
    assert failure.retryable is True
    # A wedged session belongs to THIS topic's screen, not to the box — counting
    # it against the machine would quarantine a healthy host and drag unrelated
    # topics off it.
    assert failure.host_scoped is False


def test_the_platforms_own_wording_no_longer_decides_anything():
    """平台自己写的那三句话，改成什么样都不再影响分类——反过来，别处冒出一句
    长得像的文字也不会被误判成它。

    这正是过去做不到的：判断读的就是这句话的开头/片段，于是文案既不能改、也
    不能缩，而任何一处巧合的措辞都能冒名顶替。
    """
    from app.domain.agent.platform_failures import (
        DEVICE_OFFLINE_MESSAGE,
        PROMPT_UNDELIVERED_MESSAGE,
        TURN_TIMEOUT_MESSAGE,
        classify_platform_failure,
    )

    for sentence in (
        PROMPT_UNDELIVERED_MESSAGE,
        TURN_TIMEOUT_MESSAGE,
        DEVICE_OFFLINE_MESSAGE,
        f"tmux {TURN_TIMEOUT_MESSAGE}",
    ):
        assert classify_platform_failure(sentence) is None, sentence


def test_an_offline_device_declares_itself_through_a_wrapping_raise():
    """设备连不上是平台自己判定的，所以异常自己带着码——而且要能穿过包装。

    抛出的地方和把它变成一条轮次结果的地方隔着好几层，中间常有 `raise X from
    exc`：码只看最外层就会在这里丢掉。
    """
    from app.domain.agent.hooks_substrate import ScreenSetupError
    from app.domain.agent.platform_failures import (
        DEVICE_OFFLINE_MESSAGE,
        HOST_UNREACHABLE,
        HOST_UNREACHABLE_CODE,
        classify_platform_failure,
    )

    inner = ScreenSetupError(DEVICE_OFFLINE_MESSAGE, failure_code=HOST_UNREACHABLE_CODE)
    assert classify_platform_failure(inner) is HOST_UNREACHABLE

    try:
        try:
            raise inner
        except ScreenSetupError as exc:
            raise RuntimeError("包了一层") from exc
    except RuntimeError as wrapped:
        assert classify_platform_failure(wrapped) is HOST_UNREACHABLE


def test_a_setup_failure_the_platform_cannot_name_stays_unnamed():
    """没有码的 ScreenSetupError（比如「这个话题上已有工作正在运行」）不该被
    硬塞进某个分类里——不知道就是不知道，runtime 有专门的一条路走它。"""
    from app.domain.agent.hooks_substrate import ScreenSetupError
    from app.domain.agent.platform_failures import classify_platform_failure

    assert classify_platform_failure(ScreenSetupError("说不清的失败")) is None
