import errno

import pytest

from app.domain.agent.platform_failures import (
    HOST_SCOPED_CODES,
    RUNTIME_IMAGE_MISSING_CODE,
    STORAGE_EXHAUSTED_CODE,
    WORKSPACE_VCS_PERMS,
    WORKSPACE_VCS_PERMS_CODE,
    classify_platform_failure,
    classify_session_start,
    is_storage_exhausted,
)
from app.domain.repository.service import WorkspacePermissionError


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
    meta = failure.meta
    assert (meta["event_type"], meta["code"], meta["severity"]) == (
        "platform_error",
        "storage_exhausted",
        "error",
    )
    assert meta["retryable"] is True
    # 平台提示统一契约: 卡面留一句，解释性的几句进 detail 由前端折叠。
    assert meta["title"] and meta["detail"]
    assert failure.content.count("。") == 1
    # 脱敏在两处都要成立 —— 把长文挪进 detail 不是把它挪出审查范围。
    assert "/home/private" not in failure.content
    assert "/home/private" not in failure.detail


def test_missing_runtime_image_is_a_sanitized_platform_event():
    failure = classify_platform_failure(
        "screen setup failed: Unable to find image "
        "'cheesex-agent-sandbox:latest' locally: pull access denied"
    )

    assert failure is not None
    assert failure.code == RUNTIME_IMAGE_MISSING_CODE
    meta = failure.meta
    assert (meta["event_type"], meta["code"], meta["severity"]) == (
        "platform_error",
        "runtime_image_missing",
        "error",
    )
    assert meta["retryable"] is True
    # 平台提示统一契约: 卡面留一句，解释性的几句进 detail 由前端折叠。
    assert meta["title"] and meta["detail"]
    assert failure.content.count("。") == 1
    assert "pull access denied" not in failure.content
    assert "pull access denied" not in failure.detail


def test_workspace_vcs_perms_is_carried_not_recognised_from_its_copy():
    """The failure declares itself. It is the platform's own sentence, so the
    classifier must not be reading it — copy a classifier greps is copy nobody
    can edit."""
    assert (
        classify_platform_failure(WorkspacePermissionError("工作区仓库里有…"))
        is WORKSPACE_VCS_PERMS
    )


def test_workspace_vcs_perms_walks_exception_chain():
    """Production wraps it twice (ValidationError → ScreenSetupError)."""
    try:
        try:
            raise WorkspacePermissionError("工作区仓库里有…")
        except WorkspacePermissionError as exc:
            raise RuntimeError("tmux 后端启动失败") from exc
    except RuntimeError as wrapped:
        assert classify_platform_failure(wrapped) is WORKSPACE_VCS_PERMS


def test_workspace_vcs_perms_does_not_guess_from_any_permission_error():
    """Plenty of unrelated failures say "Permission denied", and none of them
    means the store belongs to another uid."""
    assert classify_platform_failure("git push failed: Permission denied") is None
    assert classify_platform_failure("PermissionError: [Errno 13] '/tmp/x'") is None


def test_workspace_vcs_perms_payload_is_stable_and_sanitized():
    failure = classify_platform_failure(
        WorkspacePermissionError(
            "工作区仓库里有当前进程（uid=1001）无权访问的文件。"
            "原始报错：fatal: not a git repository: /ws/p/.git/worktrees/topic_x"
        )
    )

    assert failure is not None
    assert failure.code == WORKSPACE_VCS_PERMS_CODE
    meta = failure.meta
    assert (meta["event_type"], meta["code"], meta["severity"]) == (
        "platform_error",
        "workspace_vcs_perms",
        "error",
    )
    assert meta["retryable"] is True
    # 平台提示统一契约: 卡面留一句，解释性的几句进 detail 由前端折叠。
    assert meta["title"] and meta["detail"]
    assert failure.content.count("。") == 1
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
    from app.domain.agent.harness.channel import ScreenSetupError
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
    from app.domain.agent.harness.channel import ScreenSetupError
    from app.domain.agent.platform_failures import classify_platform_failure

    assert classify_platform_failure(ScreenSetupError("说不清的失败")) is None


# What sessions that died on their way up left in their runner's log. The first
# three are from dev's session host (2026-09-25), paths shortened; the rest are
# the shapes the same programs print for the other known causes.
_RECORD = (
    "cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:\n"
)
_LEASE = (
    _RECORD + "Traceback (most recent call last):\n"
    '  File "/h/client.py", line 544, in _take_leased_machine\n'
    "    client.acquire(deadline=time.monotonic())\n"
    '  File "/h/executor_transport.py", line 515, in acquire\n'
)
_START_LOGS = {
    "lease answered 504": (
        _LEASE + "executor_transport.PlatformHTTPError: Platform HTTP 504: "
        '{"code":504,"message":"GatewayTimeoutError"}',
        "Claude Code 启动失败：这个频道的工作电脑还在准备",
    ),
    "lease found no machine": (
        _LEASE + 'raise RuntimeError(result["unavailable"])\n'
        "RuntimeError: 工作电脑未连接；对话和平台工具仍可用。",
        "Claude Code 启动失败：这个频道的工作电脑没有连接",
    ),
    "lease found the room's machine unbound": (
        _LEASE + 'raise RuntimeError(result["unavailable"])\n'
        "RuntimeError: 这个频道选的工作电脑已经解绑，需要重新选择工作电脑；"
        "对话和平台工具仍可用。",
        "Claude Code 启动失败：这个频道选的工作电脑已经解绑，需要重新选择",
    ),
    "lease refused otherwise": (
        _LEASE + "executor_transport.PlatformHTTPError: Platform HTTP 403: "
        '{"message":"Device is not hosted"}',
        "Claude Code 启动失败：没能取得这个频道的工作电脑",
    ),
    "docker run exit 125": (
        _RECORD + "subprocess.CalledProcessError: Command '['docker', 'run', "
        "'--detach', 'cheese-private-executor:2.1.282']' returned non-zero exit "
        "status 125.",
        "Claude Code 启动失败：执行容器没能创建",
    ),
    "executor image missing": (
        _RECORD + "Unable to find image 'cheese-private-executor:2.1.282' locally\n"
        "docker: Error response from daemon: pull access denied for "
        "cheese-private-executor",
        "Claude Code 启动失败：机器上缺少执行容器的镜像",
    ),
    "another runner holds the lock": (
        "cheese-runner 0f0f ended: the runner failed\n"
        "Traceback (most recent call last):\n"
        '  File "/h/runner.pyz/app/domain/agent/harness/driven/runner.py", '
        "line 52, in start\n"
        "    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "BlockingIOError: [Errno 11] Resource temporarily unavailable",
        "Claude Code 启动失败：这个频道上一个会话进程还没有退出",
    ),
    "binary missing (dash)": (
        _RECORD + "sh: 1: exec: /opt/cheese/claude/versions/2.1.282: not found",
        "Claude Code 启动失败：机器上缺少 Claude Code",
    ),
    "binary missing (bash)": (
        _RECORD + "sh: line 1: /usr/local/bin/node: No such file or directory",
        "Claude Code 启动失败：机器上缺少 node",
    ),
    "a wrapper's program missing": (
        _RECORD + "/h/.local/bin/claude: line 6: exec: /h/.local/bin/claude-switchboard"
        ": cannot execute: No such file or directory",
        "Claude Code 启动失败：机器上缺少 claude-switchboard",
    ),
    "a spawned program missing": (
        _RECORD + '  File "/usr/lib/python3.12/subprocess.py", line 1955, in '
        "_execute_child\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'docker'",
        "Claude Code 启动失败：机器上缺少 docker",
    ),
    "binary for another platform": (
        _RECORD + "OSError: [Errno 8] Exec format error: '/h/.cheese/bin/claude'",
        "Claude Code 启动失败：机器上的 Claude Code 无法运行，可能已损坏或平台不符",
    ),
    "model login gone": (
        _RECORD + "Please run /login · API Error: 401 OAuth access token has been "
        "revoked",
        "Claude Code 启动失败：模型服务的登录已失效，需要管理员重新登录",
    ),
    "platform refused the session's credential": (
        _RECORD + "executor_transport.PlatformHTTPError: Platform HTTP 401: "
        '{"message":"credential expired"}',
        "Claude Code 启动失败：平台没有接受这个会话的凭证",
    ),
    "platform failed": (
        _RECORD + "executor_transport.PlatformHTTPError: Platform HTTP 500: "
        '{"message":"Internal Server Error"}',
        "Claude Code 启动失败：启动时平台返回了错误",
    ),
    "a file some code expected": (
        _RECORD + "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/h/.cheese/remote-execution/target.json'",
        "Claude Code 启动失败：原因没能识别，启动记录在现场",
    ),
    "nothing recognisable": (
        _RECORD + '[claude-code:unrecognized_model] {"model":"x"}\nKeyError: \'mode\'',
        "Claude Code 启动失败：原因没能识别，启动记录在现场",
    ),
}


@pytest.mark.parametrize("case", sorted(_START_LOGS))
def test_a_session_that_did_not_start_gets_one_sentence(case):
    log, sentence = _START_LOGS[case]

    failure = classify_session_start(log)

    assert failure.content == sentence
    assert "\n" not in failure.content
    # Carried as a code, the classification is found again on the other side
    # of the turn boundary, and none of these indicts the machine.
    assert classify_platform_failure("", code=failure.code) is not None
    assert failure.code not in HOST_SCOPED_CODES


def test_the_sentence_names_the_harness_that_was_starting():
    log, _ = _START_LOGS["lease answered 504"]

    assert (
        classify_session_start(log, harness="Codex").content
        == "Codex 启动失败：这个频道的工作电脑还在准备"
    )


def test_a_session_that_never_ended_nor_came_up_says_it_ran_out_of_time():
    failure = classify_session_start(
        "dial unix /tmp/cheese-execution-1000-x.sock: connect: no such file or "
        "directory",
        timed_out=True,
    )

    assert (
        failure.content == "Claude Code 启动失败：在等待时限内没有起来，启动记录在现场"
    )
