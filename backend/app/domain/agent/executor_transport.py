"""Room executor transport shared by agent harnesses."""

import base64
import hashlib
import json
import logging
import math
import os
import select
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

# The task checkout this session starts in. Shipped beside this module in a
# session host's bundle, where it is found by its bare name (`release.sources`).
if __package__:
    from app.domain.agent.task_checkout import names_a_directory, task_checkout
else:
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from task_checkout import names_a_directory, task_checkout

# How long a request waits out a platform endpoint that is not listening.
# An app deploy recreates the backend container, and the central session reaches
# it directly, so every tool call and every chat publication in every live room
# gets ECONNREFUSED for as long as the recreate takes — measured 2026-09-15:
# a room went four minutes with `Bash`, `Read` and chat all refused, and the
# agent read that as the sandbox being broken. Waiting is the honest answer: a
# refused connect means the request never left this process, so nothing can have
# happened twice, and the deploy that caused it ends by itself.
CONNECT_RETRY_WINDOW_S = 180
CONNECT_RETRY_MAX_DELAY_S = 5

logger = logging.getLogger(__name__)

# 手够不着时 agent 读到的那一句（结论 23）。它的家就在这里：这个文件既是后端
# import 的那一份（`private_chat.py`、`codex/tools.py` 走的都是它），又是原样发到机
# 器上、在那边没有任何我们的东西可 import 地跑起来的那一份
# (`remote_execution/release.py`)。所以这句话只有一处声明，没有第二份要同步。
#
# 一句能力话，没有平台内部术语，也没有裸 HTTP 状态码：状态码告诉 agent 的是「有东
# 西坏了」，而它这一轮需要知道的是还剩哪些通路。它替掉的是
# `f"Executor HTTP request failed: {status}"`——一个裸数字会把它送回自己的工具调用
# 里找 bug，而后端本来写好的中文 body 和 `X-Device-Id` 头在这条路上早就全丢了。
MACHINE_OUT_OF_REACH = (
    "这台机器现在够不着：文件、命令、项目 MCP 不可用；对话、记忆、平台工具可用。"
)

# 但「够不着」是关于机器的一句断言，不是「非 200」的同义词。光看状态码判得出来的只
# 有这三个：502/504 是中间那一跳转不过去，503 是执行器没在听。还有一个得连形状一起看
# 的 409（`_device_is_offline`）：链路断了那一种同样够不着，代际冲突那一种机器好好的。
# 其余的 —— 500 是机器上某个工具处理函数抛了异常，401 是执行令牌过期，别的 4xx 是那
# 台机器上的执行器比后端旧 —— 手好好的，下一次工具调用照样通。把它们也说成够不着，
# agent 会照着这句话放弃这一轮全部文件与命令操作、并向人报告机器掉线，而那是假话。
OUT_OF_REACH_STATUSES = frozenset({502, 503, 504})
# How long after the platform handed out a session's hands a Bash command's
# start goes on that admission (`RemoteClient.call`). Its permission check
# takes them a moment before the build starts the command; this only has to
# outlast that gap, and is short so a start never runs on an admission another
# call's failure has since put in doubt.
ADMISSION_REUSE_S = 10
# The connection owner's mark on a call it turned away because it is being
# released (`device_connection_app.DRAINING_HEADER`): like a refused connect,
# nothing was dispatched, so the same call is sent again.
OWNER_DRAINING_HEADER = "X-Device-Connection-Draining"

# 够不着以外的那些。同样不给裸状态码（结论 23）：数字会把 agent 送回自己的工具调用
# 里找 bug。数字和响应体进的是进程日志 —— agent 读不到它们，平台读得到。
EXECUTOR_CALL_FAILED = "这次调用失败了，机器还在：其他工具照常可用，这一个可以重试。"

# 这次会话的凭证只读工作机器（文档芝士借房间的机器），而这一次要的是读以外的事
# （`routes/execution.py` 的 `_reads`）。说成「可以重试」，agent 会照着去重试、
# 约时间再试，并告诉人机器坏了；可机器好好的，重试只会再被拒一次。
READ_ONLY_REFUSED = (
    "这个会话对工作机器只读：可以看文件和 git 记录，不能执行命令、不能改文件。"
    "这不是故障，重试结果一样。"
)

# 这次会话的改动留不下（支线，`sandbox_auth` 的 ``scratch``），
# 而这一次要把改动带出机器（同步、项目的 MCP 服务），或者机器不归它独占、它只能读
# （`routes/execution.py`）。两种都不是故障。
SCRATCH_REFUSED = (
    "这个会话在支线里，改动留不下：不能同步改动，也不能用项目的 MCP 服务；工作机器"
    "不归这个会话独占时（整台机器授权的设备、整台云虚拟机），只能读文件。这由会话所在"
    "的位置决定，不由机器决定：不是故障，重试或换机器结果都一样；有人要做这件事时，"
    "用 cheese_task(action=open) 把支线转为任务。"
)

# 链路断在这次调用的半路（`_link_interrupted`）。机器多半几秒后就回来，所以不是
# 够不着；可这一次做没做过不知道，所以也不能说「可以重试」—— 照做一遍，改动就可能
# 做两次。
LINK_INTERRUPTED = (
    "执行中与机器的连接断了一下，这次操作可能已经执行，也可能没有；"
    "先查看结果，再决定要不要重做。其他工具照常可用。"
)


# 这个会话的凭证不再归它所有：拿机器那一步（`acquire`）被拒，理由是凭证不认这个
# 会话（`session_work._attempt`），或者会话已经属于另一段对话（`routes/execution.py`）。
# 房间改成任务、会话换了房间时就是这样。凭证里的声明是签死的，新凭证又只能从同一个
# 被拒的请求里拿，所以这个进程里以后每一次都会被拒 —— 10-05 一个房间里的会话照旧
# 每两秒重试一次，连拒几个小时。说「可以重试」是假话，所以这句话叫它停。
SESSION_ENDED = (
    "平台已经收回了这个会话用机器的凭证（它所在的房间或任务换了归属）："
    "这个会话里要用机器的工具都不会再成功，重试结果一样。停下来，这次就到这里。"
)
_DEAD_CREDENTIAL = (
    "Execution credential does not own this session",
    "This credential works another conversation",
)
#: The same refusal said as a catalog sentence. Its words are the reader's
#: language, and this bundle runs on a machine that may speak none of ours; the
#: key beside them does not change with it (`app/core/errors.py` writes
#: `error.i18n`). A task's credential reaching another conversation is said
#: this way (`api/auth/resolver.py`), and is just as dead on the lease path.
_DEAD_CREDENTIAL_KEYS = frozenset({"taskCredentialOtherConversation"})

# 平台的一个接口接连答 5xx：再发也是一样，只是让 agent 每一两秒重试一次（10-04 一个
# 会话对拿机器那一步连着 376 次）。同一个接口（方法加路径）连续 `BREAKER_AFTER` 次之
# 后，这段时间内发给它的请求不发出去，直接给这句话；时间从 `BREAKER_WINDOW_S[0]` 秒
# 起，每再断一次翻倍，到 `BREAKER_WINDOW_S[1]` 为止。这个接口任何一个不是 5xx 的应答
# 都把它清掉。按接口算，不按整个平台：一个接口可以照常地答 503（比如它要的沙箱已经
# 收回），那不该让别的工具也停下。504 不算：那是前面一跳等不及，平台还在准备机器
# （`acquire`）。
PLATFORM_FAILING = (
    "平台的这个接口接连出错，这个会话 {seconds} 秒内不再向它发请求：现在重试结果一样，"
    "{seconds} 秒后再试。"
)
BREAKER_AFTER = 3
BREAKER_WINDOW_S = (30, 300)
_BREAKER_STATUSES = frozenset({500, 502, 503})


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _dead_credential(error) -> bool:
    """Whether a refused lease says this credential can never take its machine."""
    if error.status != 403:
        return False
    try:
        body = json.loads(error.body)
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    detail = body.get("error")
    if not isinstance(detail, dict):
        detail = {}
    # The platform says it in `error.retryable` (`format_error_response`).
    if detail.get("retryable") is True or body.get("retryable") is True:
        return False
    message = f"{body.get('message', '')} {detail.get('message', '')}"
    if any(text in message for text in _DEAD_CREDENTIAL):
        return True
    i18n = detail.get("i18n")
    return isinstance(i18n, dict) and i18n.get("key") in _DEAD_CREDENTIAL_KEYS


class PlatformHealth:
    """What this session has learned about the platform: a credential it
    refused for good (``dead``, by fingerprint) and, per route, its run of
    5xx answers (``routes``).

    A Claude Code session starts one process per command, so the state lives
    in a file beside the session's target (`target_file`) when there is one;
    a harness that keeps one client for the session (Codex, pi) holds it in
    memory. Writers race only to lose a count, never to corrupt the file
    (`_replace_file`)."""

    def __init__(self, config):
        target = config.get("target_file")
        self.path = Path(target).with_name("platform-health.json") if target else None
        self.memory = {}

    def read(self):
        if self.path is None:
            return dict(self.memory)
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def write(self, state):
        if self.path is None:
            self.memory = dict(state)
        else:
            _replace_file(self.path, json.dumps(state))

    def check(self, token=None, route=None):
        """Raise instead of sending, while the platform is known to refuse."""
        state = self.read()
        if token is not None and state.get("dead") == _fingerprint(token):
            raise RuntimeError(SESSION_ENDED)
        failing = (state.get("routes") or {}).get(route) or {}
        wait = failing.get("until", 0) - time.time()
        if wait > 0:
            raise RuntimeError(PLATFORM_FAILING.format(seconds=math.ceil(wait)))

    def answered(self, route, status):
        state = self.read()
        kept = state.get("routes")
        routes: dict = kept if isinstance(kept, dict) else {}
        if status not in _BREAKER_STATUSES:
            if route in routes:
                del routes[route]
                state["routes"] = routes
                self.write(state)
            return
        failing = routes.get(route) or {}
        failing["failures"] = failing.get("failures", 0) + 1
        if failing["failures"] >= BREAKER_AFTER:
            failing["trips"] = failing.get("trips", 0) + 1
            first, most = BREAKER_WINDOW_S
            failing["until"] = time.time() + min(
                first * 2 ** (failing["trips"] - 1), most
            )
        routes[route] = failing
        state["routes"] = routes
        self.write(state)

    def refused_for_good(self, token):
        state = self.read()
        state["dead"] = _fingerprint(token)
        self.write(state)


def _device_is_offline(response) -> bool:
    """Whether this answer says the hands are gone rather than one call went wrong.

    The canonical rule is ``device_hub_rpc.py:245-247``'s: on 409, and only
    there, ``X-Device-Id`` is the signal. The one producer of an offline answer
    this call path can receive is ``core/errors._handle_device_offline``
    (``DeviceOffline`` / ``DeviceUnreachable``, which serialize under that one
    name). ``device_connection_app.call``'s header-only 409 is not one this
    classifier reads: its sole client is ``DeviceHubRPC._call_owner``, which
    already classifies it before any answer is re-emitted. A ``ConflictError``
    ("Execution generation is no longer current") is also 409 and is NOT out of
    reach: the machine is fine, only the lease is stale. Lumping both into
    ``EXECUTOR_CALL_FAILED`` ("机器还在") is what made a dead websocket look like
    a retryable one-call failure while chat and platform tools kept working.
    """
    return response.status == 409 and response.getheader("X-Device-Id") is not None


def _claimed(token: str, claim: str) -> bool:
    """Whether this execution credential carries ``claim``
    (``sandbox_auth.bind_resource_token``): ``ro``, issued to only read the
    machine, or ``scratch``, whose work is not kept. Read off the token's own
    claims: the refusal's body is the backend's wording, not a contract."""
    body = (token.split(".") + ["", ""])[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return False
    return isinstance(claims, dict) and claims.get(claim) is True


def keeps_nothing(token: str) -> bool:
    """Whether this execution credential's work stays on its machine: a
    支线's."""
    return _claimed(token, "scratch")


def _link_interrupted(response) -> bool:
    """Whether the link went down under this very call: an offline answer that
    also carries ``X-Device-Link: interrupted`` (``device_hub.offline_headers``)."""
    return (
        _device_is_offline(response)
        and response.getheader("X-Device-Link") == "interrupted"
    )


# What a tool call waiting for its machine tells the agent, once, when the wait
# starts: the platform says nothing else until the machine is ready or the wait
# gives up, and a call silent that long reads as a dead shell or a lost machine.
MACHINE_PREPARING = (
    "执行环境正在准备，这次操作会在它就绪后执行（最多等 {minutes} 分钟）。"
)


# What the wait gives up with when the last answer it had was a timed-out
# request rather than the platform's own word on the machine.
MACHINE_STILL_PREPARING = "执行环境仍在准备，对话和平台工具仍可用"


def failure_text(exc: BaseException) -> str:
    """What the agent reads when a tool call raised.

    A refusal written for the agent (`PlatformToolError`, `PlatformHTTPError`,
    `MachineOutOfReach`, a `ValueError` naming the problem) reads as written.
    A lookup failure does not: ``str(KeyError("id"))`` is ``'id'``, and an
    agent handed that bare word cannot tell it from an answer. Those, and an
    exception with no text, are named by their type."""
    text = str(exc)
    if text and not isinstance(exc, LookupError):
        return text
    return f"Tool call failed with {type(exc).__name__}: {text or '(no message)'}"


class MachineOutOfReach(RuntimeError):
    """够不着这件事，判得出来的那一种。

    以前调用方只能比字符串（`str(exc) == MACHINE_OUT_OF_REACH`），于是它只认得
    「执行器**答了** 502/503/504」这一档 —— 而机器真的够不着时执行器什么也不答：
    这条连接的读超时是 660 秒，到点抛的是 `TimeoutError`；连接被拒、重试窗口耗尽
    抛的是 `ConnectionRefusedError`。两者的 `str()` 都不是那句话，所以最该被认出
    来的那一档反而认不出来，而那一轮余下的每一次文件与命令调用还要各等一次 660 秒。

    说的还是同一句话（`str(exc)` 不变），agent 读到的东西一个字没动；多出来的只是
    一个调用方判得动的类型。
    """

    def __init__(self) -> None:
        super().__init__(MACHINE_OUT_OF_REACH)


# Where a session started before its machine was rented sees the project: the
# machine, and so the path it holds the project at, does not exist yet.
DEFERRED_WORKSPACE = "/unavailable-project"

#: The name a session's working directory also goes by (`remote_execution.client`
#: `prepare`): a fixed name, the same in every session, whose value is the
#: project that session sees. It never enters a command's environment — a build
#: running natively on the executor has no such variable, and a room's command
#: must see exactly what that one sees. The shell prefix binds it in the
#: command's own shell instead, and the value crosses as part of `body`, so the
#: transport respells it where it respells every other path a body names.
WORKING_DIRECTORY = "T"


def _retry_connect(attempt: int, deadline: float) -> bool:
    """Sleep before the next attempt, or say the window is over. Hand-written,
    not tenacity: this file runs on machines with the standard library only.

    ONLY for a connection that was refused: `http.client` raises that before it
    writes anything, which is what makes the replay safe. A failure any later —
    a reset, a lost response — can follow a mutation the server already
    committed, and those still travel straight up (see `call`).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(2**attempt * 0.5, CONNECT_RETRY_MAX_DELAY_S, remaining))
    return True


class PlatformHTTPError(RuntimeError):
    """The backend answered, and not with success. `status` is what a caller
    branches on (a living doc's 409 is a merge to do, not a failure)."""

    def __init__(self, status: int, body: str):
        super().__init__(f"Platform HTTP {status}: {body}")
        self.status = status
        self.body = body


def on_the_machine(invoke, command, request_id, *, timeout_ms=60000):
    """One command's stdout, from the machine that holds the work.

    The session's own host only sees a forwarded workspace, so a file there (or
    a push of the work) is reached the way every other project operation is: a
    command on the executor, through ``invoke`` — the caller's one exit to the
    machine, which trips the unreachable breaker instead of each call waiting
    out the executor's read timeout on its own.
    """
    receipt = invoke(
        {"id": request_id, "tool": "Bash"},
        {"command": command, "timeout": timeout_ms},
    )
    if "error" in receipt:
        raise RuntimeError(receipt["error"])
    value = receipt.get("value") or {}
    stdout = value.get("stdout") or ""
    if value.get("backgroundTaskId"):
        raise RuntimeError("the command on the machine did not finish")
    if stdout.startswith("Exit code"):
        # A command that exits non-zero is not an executor failure; the build
        # hands back its own error text as stdout, with the exit code on top.
        raise RuntimeError(stdout.strip())
    return stdout


def stat_file_on_the_machine(invoke, path, request_id):
    """The file's size on the machine, without walking its bytes across."""
    stdout = on_the_machine(invoke, f"wc -c < {shlex.quote(path)}", request_id)
    return int("".join(stdout.split()))


# The build returns at most 30000 characters of a command's stdout inline and
# truncates the rest. 21000 bytes encode to 28000 base64 characters plus line
# breaks, so every piece arrives whole.
MACHINE_READ_CHUNK_BYTES = 21000


def session_path(path):
    """Where a room's session sees a path of its executor: at that path.

    The session runs in a mount namespace of its own on the session host, with
    the project view at the executor's own path (`client.py` `enter`), so the
    paths it prints are the ones Claude Code on the executor would print. A
    Windows path cannot be a path on the Linux session host; the session sees
    it the way Git Bash, the shell Claude Code runs there, spells it
    (`C:\\Users\\x` is `/c/Users/x`), and the executor turns that spelling
    back into its own (`runtime.native_path`).
    """
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        rest = path[2:].replace("\\", "/").rstrip("/")
        return "/" + path[0].lower() + ("/" + rest.lstrip("/") if rest else "")
    return path


def to_machine(text, seen, workspace):
    """``text`` with the project's path as a session sees it from a directory
    of its own (``seen``: the placeholder a session starts at before its
    lease, or a member's own Claude Code's directory when its executor is on
    another machine) spelled where the executor holds the project. A Windows
    session host's shell spells that directory the Git Bash way, so both
    spellings are."""
    for spelled in {seen, session_path(seen)}:
        text = text.replace(spelled, workspace)
    return text


def read_file_on_the_machine(invoke, path, request_id):
    """One file's bytes, from the machine that holds them, in pieces small
    enough that the build hands each one back whole."""
    size = stat_file_on_the_machine(invoke, path, f"{request_id}-size")
    quoted = shlex.quote(path)
    pieces = []
    for index in range(-(-size // MACHINE_READ_CHUNK_BYTES)):
        stdout = on_the_machine(
            invoke,
            f"dd if={quoted} bs={MACHINE_READ_CHUNK_BYTES} skip={index} count=1 "
            "status=none | base64",
            f"{request_id}-{index}",
        )
        pieces.append(base64.b64decode("".join(stdout.split()), validate=True))
    data = b"".join(pieces)
    if len(data) != size:
        raise RuntimeError(f"{path} changed on the machine while it was being read")
    return data


def tool_answer(outcome: dict, receipts: Path, pictures=()) -> dict:
    """A tool call's outcome as the MCP answer the plugin (`proxy.js`) reads:
    the outcome as JSON in one text block, then pictures as image blocks.

    A native Read of an image keeps its metadata in the text and its pixels in
    an image block: base64 in text hits Claude Code's MCP text-output limit.
    MCP replaces large text with prose, and the plugin needs the original
    receipt, including API JSON and Edit state, so a large outcome is written
    under ``receipts`` and the text names the file. ``pictures`` are the ones
    a platform tool hands back (`sandbox/cheese`'s `Said`), after its text.
    """
    pictures = list(pictures)
    result = outcome.get("result", {})
    if isinstance(result, dict) and result.get("type") == "image":
        image = result["file"]
        metadata = {k: v for k, v in image.items() if k != "base64"}
        outcome = {"result": {**result, "file": metadata}}
        pictures.insert(0, {"data": image["base64"], "mimeType": image["type"]})
    encoded = json.dumps(outcome)
    if len(encoded) > 32_000:
        receipts.mkdir(exist_ok=True, mode=0o700)
        receipt_path = receipts / f"{uuid.uuid4().hex}.json"
        receipt_path.write_text(encoded)
        receipt_path.chmod(0o600)
        encoded = json.dumps({"receipt_path": str(receipt_path)})
    images = [
        {"type": "image", "data": p["data"], "mimeType": p["mimeType"]}
        for p in pictures
    ]
    return {"content": [{"type": "text", "text": encoded}, *images]}


class PlatformHost:
    """What a platform tool (`PLATFORM_TOOLS` in `sandbox/cheese`) runs against
    when the session is not on the machine (结论 63).

    The backend is reached from here, directly — that is why the table is whole
    while the machine is gone. The one thing a tool can need from the machine
    (a task's commits pushed) goes through ``invoke`` and so shares its
    breaker: gone means an immediate MACHINE_OUT_OF_REACH, never a wait.
    """

    def __init__(self, client, invoke, call_id, doc_versions, run, compact=None):
        self.client = client
        self.invoke = invoke
        self.call_id = call_id
        self.doc_versions = doc_versions
        self.environ = dict(os.environ)
        #: Which run of its session this call is made in, as the session's
        #: runner names it (``harness/epoch/seq`` of the ``running`` event that
        #: began it), or None for a harness whose runner names no runs. Every
        #: platform request carries it (``X-Cheese-Run``), so what the agent
        #: reports is known to be that run's (`presentation.task_presentation`).
        self.run = run
        #: Ask this session's harness to compact its context, for the one
        #: tool that does (`cheese_session(action=compact)`); a session with none refuses.
        self.compact = compact

    def request(self, plan):
        run = self.run()
        if run:
            plan = {**plan, "run": run}
        stdout = self.client.platform_request(plan)["value"]["stdout"]
        return json.loads(stdout) if stdout else {}

    def wait_machine(self):
        # A command that does nothing, so the wait is the one every tool call
        # makes for its hands: up to the operation deadline while the machine
        # is prepared, out of reach at once when it is gone.
        on_the_machine(self.invoke, "true", f"{self.call_id}-wait", timeout_ms=60000)

    def sync_task(self, task_id):
        # Pushing can include uploading a backup bundle; the CLI's own ceiling for
        # that upload is two minutes, the Bash tool's is ten.
        on_the_machine(
            self.invoke,
            "cheese sync --task " + shlex.quote(str(uuid.UUID(task_id))),
            f"{self.call_id}-sync",
            timeout_ms=600000,
        )


def no_run():
    """The run source of a harness whose runner names no runs: it reports no
    lifecycle (`Harness.reports_lifecycle`), so its sessions have no state on
    the platform for a run to be compared with, and its reports always stand."""
    return None


def _replace_file(path, text):
    """Replace a session file whole, readable only by its owner. Every command
    start of a leased session rewrites the token and the target (`acquire`),
    and the commands that start beside it (a background Bash and the hook
    after it, parallel Bash calls) read them at any moment: a file truncated
    and then written shows them an empty token in between."""
    temporary = f"{path}.{uuid.uuid4().hex}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    os.replace(temporary, path)


#: Where a Claude Code session's runner listens, as the runner tells the
#: session it starts (`claude_code.runner.Runner.start`, which spells the same
#: name: this file ships alone and imports nothing of it). The session's own
#: helpers reach the runner there, never the agent, whose commands run on the
#: room's machine.
SESSION_SOCKET = "CHEESE_SESSION_SOCKET"
#: The header a platform request names its session's run in (`PlatformHost.run`).
#: The same name as `room_task.progress.RUN_HEADER`, which reads it: this file
#: ships alone and imports nothing of it.
RUN_HEADER = "X-Cheese-Run"


def dial_runner(path: str, timeout: float):
    """The runner holding this session, at the socket it gave the session
    (`SESSION_SOCKET`). On Windows that names the file holding the runner's
    loopback port and the token it wants before a request (`driven/runner.py`)."""

    if sys.platform == "win32":
        with open(path) as named:
            endpoint = json.load(named)
        connection = socket.create_connection(
            ("127.0.0.1", endpoint["port"]), timeout=timeout
        )
        connection.sendall(endpoint["token"].encode() + b"\n")
        return connection
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(timeout)
    connection.connect(path)
    return connection


def register_project_hooks(config, hooks):
    """Give a running Claude Code session the project's own tool hooks.

    The session registers them from the machine when it starts
    (`client.prepare`). One started before it had a machine had none to
    register, so this runs when the machine attaches: the runner hands the
    hooks to the build as flag settings (`apply_flag_settings`), which take
    effect before the answer comes back, so every later tool call fires them,
    as it would in a session started on the machine. Returns whether the
    session's hooks changed. A target with no settings of its own (Codex: the
    executor runs the hooks around its calls) has nothing to change.
    """

    target_file = config.get("target_file")
    if not config.get("central_config") or not target_file:
        return False
    record = Path(target_file).with_name("project-hooks.json")
    previous = json.loads(record.read_text()) if record.exists() else {}
    if previous == hooks:
        return False
    path = os.environ.get(SESSION_SOCKET)
    if not path:
        raise RuntimeError(
            "This session cannot take up the project's tool hooks: "
            "it was started without its runner's socket"
        )
    request = {"method": "apply_hooks", "params": {"hooks": hooks}}
    with dial_runner(path, 60) as connection:
        connection.sendall(json.dumps(request).encode() + b"\n")
        answer = json.loads(connection.makefile("rb").readline())
    response = answer.get("result") or {}
    if "error" in answer or response.get("subtype") != "success":
        raise RuntimeError(
            "The session did not take up the project's tool hooks: "
            + str(answer.get("error") or response.get("error") or response)
        )
    _replace_file(str(record), json.dumps(hooks))
    return True


#: Starts the error the executor refuses to start a local server with, on a
#: member's computer whose owner has not allowed its command (`runtime.py`).
APPROVAL_REQUIRED = "MCP_APPROVAL_REQUIRED "


def _remote_servers(target: dict) -> list[str]:
    return list((target.get("remote_mcp") or {}).get("servers", []))


def command_servers(target: dict) -> list[str]:
    """The commands added in project settings and the teammate's type's own
    (`mcp_commands`): they run on the room's machine, which is handed each
    one's definition with the call. A name the checkout's `.mcp.json` or a
    remote server already uses stays that server's, as committed configuration
    decides."""
    taken = {*target.get("mcp_servers", []), *_remote_servers(target)}
    return [name for name in target.get("mcp_commands") or {} if name not in taken]


def session_servers(target: dict) -> list[str]:
    """Every MCP server a session lists: the machine's stdio servers and the
    commands handed to it, once the session is on the machine, and the remote
    ones.

    Before then (a session at the placeholder) it lists no stdio server at
    all: listing one would take the machine for a turn that may never need it.
    The session is relaunched onto the machine once it has one, and lists them
    then."""
    on_machine = not (
        target.get("kind") == "deferred"
        and target.get("workspace") == DEFERRED_WORKSPACE
    )
    return [
        *(
            [*target.get("mcp_servers", []), *command_servers(target)]
            if on_machine
            else []
        ),
        *_remote_servers(target),
    ]


class RemoteClient:
    def __init__(self, config, *, shared_connection=False):

        self.config = config
        self.transport = SimpleNamespace() if shared_connection else threading.local()
        self.publication_lock = threading.Lock()
        self.publication = None
        self.platform_lock = threading.Lock()
        self.platform = None
        self.health = PlatformHealth(config)
        # One session's tool calls run on several threads (`transport`): the
        # working directory is resolved once, under this lock, and a thread
        # that is already inside the resolve (the call it makes to ask the
        # machine) must not wait for itself.
        self.working_directory_lock = threading.RLock()
        self.resolving_working_directory = threading.local()

    def execution_token(self):
        if self.config.get("execution_token"):
            return self.config["execution_token"]
        token_file = self.config.get("token_file")
        if token_file:
            with open(token_file) as stream:
                return stream.read().strip()
        return os.environ["CHEESE_TOKEN"]

    def platform_token(self):
        # A session answering someone's question calls the platform with the
        # credential minted for that question, never the one it started with.
        return self.config.get("platform_token") or (
            self.execution_token()
            if self.config.get("token_file")
            else os.environ.get("CHEESE_TOKEN", "")
        )

    def _transcript(self, query):
        """This session's transcript, as the platform answers for ``query``.

        The transcript itself is on the session host, which this machine does
        not reach; the platform relays the question to the session's runner
        and hands back what it says (`topic_transcript_chunks`). Nothing the
        caller writes names a path: which file is meant is this session's
        own, decided by the credential and the room it names.
        """
        topic = os.environ.get("CHEESE_TOPIC")
        if not topic:
            raise RuntimeError(  # i18n-exempt: launch invariant, never shown
                "This session cannot read its transcript: it names no room"
            )
        answer = self.platform_request(
            {
                "method": "GET",
                "path": f"/topics/{topic}/session-transcript?{query}",
            }
        )
        payload = json.loads(answer["value"]["stdout"])
        data = payload.get("data")
        if not isinstance(data, dict):
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise RuntimeError("The platform answered nothing about the transcript")
        return data

    def transcript_chunk(self, params):
        """One chunk of this session's transcript: an offset and a size."""
        query = f"offset={int(params['offset'])}&size={int(params['size'])}"
        return self._transcript(query)

    def transcript_path(self):
        """Where the session host spells this session's transcript, or None.

        The path is the session host's, and the build names it in what it
        tells the model after a compaction. This machine mounts its read-only
        view at exactly that path (`bootstrap.py` `mount_transcript`), so the
        name resolves where the model's tools run.
        """
        return self._transcript("path=1").get("path")

    def platform_request(self, args):
        method = args.get("method", "GET").upper()
        path = args.get("path", "")
        parsed = urlsplit(path)
        if (
            method not in ("GET", "POST", "PUT", "PATCH", "DELETE")
            or not path.startswith("/")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise ValueError("Platform requests require a relative API path and method")
        api = os.environ.get("CHEESE_API", "").rstrip("/")
        token = self.platform_token()
        if not api or not token:
            raise RuntimeError("Platform requests require room credentials")
        route = f"{method} {parsed.path}"
        self.health.check(route=route)
        with self.platform_lock:
            if self.platform is None or self.platform.config["url"] != api:
                if self.platform is not None:
                    previous = getattr(self.platform.transport, "connection", None)
                    if previous is not None:
                        previous.close()
                self.platform = RemoteClient({"url": api}, shared_connection=True)
            transport = self.platform
            connection, base = transport.connection()
            try:
                connection.request(
                    method,
                    base.rstrip("/") + path,
                    body=json.dumps(args["body"]).encode() if "body" in args else None,
                    headers={
                        **transport.transport.headers,
                        "Content-Type": "application/json",
                        # What a person's 芝士 does for them once its gate let
                        # it is done with the pass it was given, in place of
                        # its own credential (`assistant.passes`).
                        "Authorization": f"Bearer {args.get('pass') or token}",
                        **(
                            {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                            if os.environ.get("CHEESE_TURN")
                            else {}
                        ),
                        # The run of the session this call is made in
                        # (`PlatformHost.run`).
                        **({RUN_HEADER: args["run"]} if args.get("run") else {}),
                    },
                )
                response = connection.getresponse()
                body = response.read().decode()
                self.health.answered(route, response.status)
                if not 200 <= response.status < 300:
                    raise PlatformHTTPError(response.status, body)
            except Exception:
                connection.close()
                transport.transport.connection = None
                raise
        return {"value": {"stdout": body, "stderr": ""}}

    def connection(self):
        # Shell forwarding exits before creating a client; keep its startup
        # independent of HTTP, TLS and proxy discovery imports.
        # deferred-import: shell forwarding exits before a client is made
        import http.client

        # deferred-import: shell forwarding exits before a client is made
        from urllib.request import getproxies, proxy_bypass

        if getattr(self.transport, "connection", None) is not None:
            connection = self.transport.connection
            # Idle HTTP connections can be closed by the gateway between turns.
            # Reconnect before writing when the socket already has EOF/data.
            if connection.sock and select.select([connection.sock], [], [], 0)[0]:
                connection.close()
            return connection, self.transport.path
        target = urlsplit(str(self.config["url"]))
        if not target.hostname:
            raise ValueError("Executor URL requires a hostname")
        proxy = (
            getproxies().get(target.scheme)
            if not proxy_bypass(target.hostname)
            else None
        )
        address = urlsplit(proxy) if proxy else target
        hostname = address.hostname
        if not hostname:
            raise ValueError("Executor proxy URL requires a hostname")
        factory = (
            http.client.HTTPSConnection
            if address.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = factory(hostname, address.port, timeout=660)
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        self.transport.headers = {}
        if proxy:
            headers = {}
            if address.username is not None:
                auth = unquote(address.username) + ":" + unquote(address.password or "")
                headers["Proxy-Authorization"] = (
                    "Basic " + base64.b64encode(auth.encode()).decode()
                )
            if target.scheme == "https":
                if address.scheme != "http":
                    raise ValueError(
                        "Executor HTTPS requests require an HTTP CONNECT proxy"
                    )
                connection = http.client.HTTPSConnection(
                    hostname, address.port or 80, timeout=660
                )
                connection.set_tunnel(target.hostname, target.port or 443, headers)
            else:
                path = self.config["url"]
                self.transport.headers = headers
        self.transport.connection, self.transport.path = connection, path
        return connection, path

    def publish_message(self, payload, args):
        with self.publication_lock:
            return self._publish_message(payload, args)

    def _publish_message(self, payload, args):
        content = args.get("content") or ""
        attachments = args.get("attachments") or []
        if not isinstance(content, str) or (not content.strip() and not attachments):
            raise ValueError("Chat content must be a nonempty string")
        api = os.environ.get("CHEESE_API", "").rstrip("/")
        topic = os.environ.get("CHEESE_TOPIC", "")
        token = os.environ.get("CHEESE_TOKEN", "")
        if not api or not topic or not token:
            raise RuntimeError("Chat publication requires room credentials")
        publication_id = str(
            uuid.UUID(args["request_id"])
            if args.get("request_id")
            else uuid.uuid5(
                uuid.NAMESPACE_URL, f"{topic}/{payload['session_id']}/{payload['id']}"
            )
        )
        url = f"{api}/topics/{topic}/messages"
        if self.publication is None or self.publication.config["url"] != url:
            # Publication is serialized across MCP workers; its connection must
            # outlive the worker thread that happened to make the first call.
            self.publication = RemoteClient({"url": url}, shared_connection=True)
        publisher = self.publication
        body = json.dumps(
            {
                "content": content,
                "request_id": publication_id,
                **({"reply_to": args["reply_to"]} if args.get("reply_to") else {}),
                **({"attachments": attachments} if attachments else {}),
            }
        ).encode()
        deadline = time.monotonic() + CONNECT_RETRY_WINDOW_S
        attempt = 0
        while True:
            try:
                result = self._publish_once(publisher, body, token, publication_id)
                break
            except ConnectionRefusedError:
                # The room is mid-deploy: nothing was sent, so waiting cannot
                # publish the same message twice.
                if not _retry_connect(attempt, deadline):
                    raise RuntimeError(
                        "Chat publication failed; the platform refused connections "
                        f"for {CONNECT_RETRY_WINDOW_S}s; "
                        f"request_id={publication_id}"
                    ) from None
                attempt += 1
        return {
            "value": {
                "stdout": json.dumps(result["data"], ensure_ascii=False),
                "stderr": f"[cheese] request_id={publication_id}",
                "interrupted": False,
                "noOutputExpected": False,
                "returnCodeInterpretation": "Exit code 0",
            }
        }

    @staticmethod
    def _publish_once(publisher, body, token, publication_id):
        connection, path = publisher.connection()
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    **publisher.transport.headers,
                    **(
                        {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                        if os.environ.get("CHEESE_TURN")
                        else {}
                    ),
                },
            )
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError(
                    f"Chat publication failed: HTTP {response.status}; "
                    f"request_id={publication_id}"
                )
            return json.loads(data)
        except ConnectionRefusedError:
            connection.close()
            publisher.transport.connection = None
            raise
        except Exception as exc:
            connection.close()
            publisher.transport.connection = None
            raise RuntimeError(
                f"Chat publication failed; request_id={publication_id}: {exc}"
            ) from exc

    def command(self, mode):
        command = [*self.config["command"], mode, "--state", self.config["state"]]
        # SSH joins all arguments after the host with spaces. Quote the remote
        # command once, rather than letting workspace names become shell syntax.
        if self.config.get("ssh"):
            command = [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.config["ssh"],
                shlex.join(command),
            ]
        return command

    def take_now(self):
        """Take this session's hands as they are, without waiting for a
        machine being prepared. False when the platform cannot hand them out
        at this moment (the machine still being prepared, gone, or refused);
        a platform that failed to answer at all still raises."""
        try:
            self.acquire(deadline=time.monotonic())
        except PlatformHTTPError:
            raise
        except RuntimeError:
            return False
        return True

    def working_directory(self, *, notice=True):
        """Where this session's commands start, and what `$T` stands for: its
        task's checkout once the machine is at hand, else the project view.

        Resolved once and kept — in this client and in the target file beside
        it, so the command processes after this one read it rather than asking
        the machine again. Only a real answer is kept: a machine that could not
        be asked (``task_checkout`` raising) is asked again at the next call,
        instead of the session's directory being fixed to the project forever.
        ``notice``: whether a failure is said on stderr (a launch says so; a
        command's stderr is the agent's)."""
        known = self.config.get("working_directory")
        if names_a_directory(known):
            return known
        topic = os.environ.get("CHEESE_TOPIC", "")
        if not topic or self.config.get("kind") == "private":
            return self.config.get("workspace")
        if getattr(self.resolving_working_directory, "yes", False):
            return self.config.get("workspace")  # asked from inside the resolve
        with self.working_directory_lock:
            known = self.config.get("working_directory")
            if names_a_directory(known):
                return known
            self.resolving_working_directory.yes = True
            try:
                checkout = task_checkout(self, topic, notice=notice)
            except Exception:  # not kept: the next call asks again
                self.config["checkout_pending"] = True
                self._save_target()
                return self.config.get("workspace")
            finally:
                self.resolving_working_directory.yes = False
            self.config.pop("checkout_pending", None)
            self.config["working_directory"] = checkout or self.config.get("workspace")
            self.config["working_directory_lease"] = self.config.get("generation")
            self._save_target()
            return self.config["working_directory"]

    def _save_target(self):
        """Write this client's config back to the target file, whole and atomically
        (`_replace_file`): the next command is a process of its own."""
        target_file = self.config.get("target_file")
        if target_file:
            _replace_file(target_file, json.dumps(self.config))

    def acquire(self, *, deadline, abandoned=None, preparing=None):
        """Take this session's hands through the platform (`lease_path`): the
        machine the lease names becomes where its calls go, and the credential
        for it is kept (`token_file`) beside the target (`target_file`).

        The platform waits a bounded while for a machine being prepared, and
        this asks again until ``deadline``; the machine still being prepared
        then, or not to be had at all, raises with the platform's reason.
        `MACHINE_PREPARING` is written to the text stream ``preparing`` once,
        as soon as the platform says the machine is being prepared, and so is
        a notice the platform has for the agent with the hands (a sandbox that
        was replaced): only a caller with a stream asks for one. Returns
        whether the hands are another lease than the ones this client held."""
        # Whoever wants to be told asks first without waiting: a waiting
        # request answers "preparing" only after the platform's bounded wait,
        # and the wait is exactly when the caller would otherwise hear nothing.
        told = preparing is None
        token = self.platform_token()
        self.health.check(token)
        while True:
            remaining = deadline - time.monotonic()
            try:
                response = self.platform_request(
                    {
                        "method": "POST",
                        "path": self.config["lease_path"],
                        "body": {
                            "env": self.config.get("setup_env", {}),
                            "timeout": max(0.001, remaining) if told else 0.001,
                            "tells_agent": preparing is not None,
                        },
                    }
                )
                result = json.loads(response["value"]["stdout"])["data"]
            except PlatformHTTPError as error:
                if _dead_credential(error):
                    self.health.refused_for_good(token)
                    raise RuntimeError(SESSION_ENDED) from error
                # A front that gave up on the request before the platform
                # answered says nothing about the machine: it is still being
                # prepared as far as anyone knows, so this asks again. Handed
                # to the agent, it read as a final failure and the agent slept.
                if error.status != 504:
                    raise
                result = {"unavailable": MACHINE_STILL_PREPARING, "preparing": True}
            if abandoned is not None and abandoned():
                raise RuntimeError("Tool call was cancelled")
            if not told:
                told = True
                if result.get("preparing"):
                    minutes = max(1, math.ceil(remaining / 60))
                    print(
                        MACHINE_PREPARING.format(minutes=minutes),
                        file=preparing,
                        flush=True,
                    )
                    continue
            # The platform waited as long as one request may while the
            # machine is prepared. Ask again until the deadline, which is what
            # bounds the wait.
            if not result.get("preparing") or time.monotonic() >= deadline:
                break
        if result.get("preparing"):
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise RuntimeError(f"{result['unavailable']}（等到操作时限仍未就绪）")
        if result.get("unavailable"):
            raise RuntimeError(result["unavailable"])
        target = result["target"]
        if result.get("notice") and preparing is not None:
            print(result["notice"], file=preparing, flush=True)
        changed_lease = self.config.get("generation") != target.get("generation")
        if "working_directory" in self.config and (
            # Another lease: a checkout resolved on the machine before is not
            # this one's, even when it holds the project at the same path.
            target.get("generation") != self.config.get("working_directory_lease")
            or target.get("workspace") != self.config.get("workspace")
        ):
            self.config.pop("working_directory", None)
            self.config.pop("working_directory_lease", None)
        self.config.update(target)
        # Wall-clock, beside the target: the command start this admits runs in
        # another process (`shell_prefix`), which reads it from there (`call`).
        self.config["admitted_at"] = time.time()
        token_file = self.config.get("token_file")
        if token_file:
            _replace_file(token_file, result["token"])
        else:
            self.config["execution_token"] = result["token"]
        target_file = self.config.get("target_file")
        if target_file:
            _replace_file(target_file, json.dumps(self.config))
        previous = getattr(self.transport, "connection", None)
        if previous is not None:
            previous.close()
            self.transport.connection = None
        return changed_lease

    def remote_mcp(self, method, params):
        """A call to one of the project's remote MCP servers. The platform holds
        its credential and makes the call (`POST /topics/{id}/mcp/{name}`), so
        it goes there and not to the room's machine, and no machine is taken
        for it. The answer has the shape the executor's would."""
        remote = self.config["remote_mcp"]
        server = params["server"]
        if method == "invoke":
            request = {
                "method": "tools/call",
                "params": {"name": params["tool"], "arguments": params.get("args", {})},
            }
        else:
            request = {"method": params["method"], "params": params.get("params") or {}}
        response = self.platform_request(
            {"method": "POST", "path": f"{remote['path']}/{server}", "body": request}
        )
        answer = json.loads(response["value"]["stdout"])["data"]
        if method == "invoke":
            return (
                {"error": answer["error"]}
                if "error" in answer
                else {"value": answer["result"]}
            )
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer["result"]

    def remote_call_with_hooks(self, call_id, server, tool, args, preparing=None):
        """A remote server's tool call with the project's PreToolUse and
        PostToolUse hooks around it, for a harness that does not fire them
        itself (Codex, at its gateway: `codex/tools.py`). The hooks run on the
        room's machine, which is taken for it; a PreToolUse deny, or a machine
        out of reach, means the call is not made. Returns what
        `call("invoke", …)` does."""
        name = f"mcp__{server}__{tool}"
        before = self.control(
            {
                "subtype": "tool_hooks",
                "event": "PreToolUse",
                "tool": name,
                "args": args,
                "request_id": call_id,
            },
            preparing=preparing,
        )
        if "denied" in before:
            return {"error": before["denied"]}
        args = before["args"]
        receipt = self.remote_mcp(
            "invoke", {"id": call_id, "server": server, "tool": tool, "args": args}
        )
        after = self.control(
            {
                "subtype": "tool_hooks",
                "event": "PostToolUse",
                "tool": name,
                "args": args,
                "request_id": call_id,
                "result": receipt.get("value", receipt.get("error")),
            }
        )
        if "denied" in after and "value" in receipt:
            # The call has happened; a PostToolUse block is feedback on its
            # result, which the model reads beside it, as in Claude Code.
            value = dict(receipt["value"])
            value["content"] = [
                *value.get("content", []),
                {"type": "text", "text": f"PostToolUse hook: {after['denied']}"},
            ]
            return {"value": value}
        return receipt

    def permission(self, payload, args, preparing=None):
        """Only the project's `permissions.deny`, read on the room's machine,
        for a call the build is about to run itself and whose hooks it fires
        itself, never having loaded the project's settings: a Claude Code
        room's Bash (`proxy.js`). Returns what `call("invoke", …)` does."""
        answer = self.control(
            {
                "subtype": "tool_hooks",
                "event": "PreToolUse",
                "tool": payload["tool"],
                "args": args,
                "request_id": payload["id"],
                "fire": False,
            },
            preparing=preparing,
        )
        return {"error": answer["denied"]} if "denied" in answer else {"value": {}}

    def remote_servers(self):
        return list((self.config.get("remote_mcp") or {}).get("servers", []))

    # --- the project's local servers -------------------------------------------
    #
    # A local server — a command the checkout's `.mcp.json`, the project's
    # settings or the teammate's type names — is started by the machine's
    # executor from the checkout's definition or the one handed with the call
    # (`mcp_commands`). Before its first call this session asks the platform two
    # things to hand it with each call (`mcp_path`/<name>/start): the values of
    # the project secrets the default branch's definition names, and, on a
    # member's own computer, the fingerprints of the commands its owner lets
    # run there; the executor checks the command it is about to run against
    # those. How the server fared is said back (`report_local`) for the
    # project's settings page. A session started without `mcp_path` (a
    # member's own Claude Code, on their own computer) asks nothing.

    def local(self, server):
        return (
            isinstance(server, str)
            and server != "native"
            and server not in self.remote_servers()
        )

    def _local_start(self, server):
        if not self.config.get("mcp_path"):
            return {}
        cache = self.__dict__.setdefault("local_starts", {})
        if server not in cache:
            response = self.platform_request(
                {
                    "method": "POST",
                    "path": f"{self.config['mcp_path']}/{quote(server, safe='')}/start",
                    "body": {"device_id": self.config.get("device_id")},
                }
            )
            cache[server] = json.loads(response["value"]["stdout"])["data"]
        return cache[server]

    def report_local(self, server, state, **more):
        """Say how a local server fared; never fails the call it is about."""
        if not self.config.get("mcp_path") or not self.local(server):
            return
        try:
            self.platform_request(
                {
                    "method": "POST",
                    "path": f"{self.config['mcp_path']}/{quote(server, safe='')}/run",
                    "body": {
                        "state": state,
                        "device_id": self.config.get("device_id"),
                        **more,
                    },
                }
            )
        except Exception:  # a report is not worth a failed call
            pass

    def local_outcome(self, server, error):
        """How a local server's first list of its tools went, said back once
        per change; answers with what to tell the session instead of the
        executor's own words, when there is something better to say."""
        if not self.local(server):
            return None
        said = self.__dict__.setdefault("local_said", {})
        if error is None:
            if said.get(server) != "running":
                said[server] = "running"
                self.report_local(server, "running")
            return None
        if APPROVAL_REQUIRED in error:
            # Wherever the transport put it in its own words, on its own line.
            line = error[error.index(APPROVAL_REQUIRED) + len(APPROVAL_REQUIRED) :]
            asked = json.loads(line.splitlines()[0])
            if said.get(server) != "awaiting":
                said[server] = "awaiting"
                self.report_local(
                    server,
                    "awaiting",
                    fingerprint=asked["fingerprint"],
                    command=asked["command"],
                )
            # The cached answer had none of what the owner may since allow.
            self.__dict__.get("local_starts", {}).pop(server, None)
            return (
                f"{server} waits for the owner of this computer to allow its "
                "command to run here. Nothing about it can be done from this "
                "session."
            )
        said[server] = "failed"
        self.report_local(server, "failed", log=error.splitlines()[-12:])
        return None

    def local_closed(self):
        """The session is going: the servers it said were running are not."""
        for server, state in list(self.__dict__.get("local_said", {}).items()):
            if state == "running":
                self.report_local(server, "idle")

    def command_servers(self):
        return command_servers(self.config)

    def session_servers(self):
        return session_servers(self.config)

    def call(self, method, params=None, *, abandoned=None, preparing=None):
        """``abandoned`` says the caller has given the operation up (a cancelled
        tool call). Acquiring hands can wait for a machine being prepared; an
        operation given up during that wait is never started. ``preparing`` is
        the text stream told when that wait starts (`acquire`).

        A Bash command's start right after the hands were taken — for its own
        permission check, which `proxy.js` makes just before (`permission`) —
        goes on that admission instead of asking the platform again. Should it
        fail for any reason, the hands are taken again and the start is sent
        once more: the command id makes a repeated start the same start."""
        admitted = self.config.get("admitted_at")
        if (
            self.config.get("lease_path")
            and self.config.get("kind") == "device"
            and method == "control"
            and (params or {}).get("subtype") == "shell"
            and (params or {}).get("operation") == "start"
            and (params or {}).get("kind") == "bash"
            and isinstance(admitted, (int, float))
            and 0 <= time.time() - admitted < ADMISSION_REUSE_S
        ):
            try:
                return self._call(method, params, admitted=True)
            except Exception:  # Any failure: retried below on new hands.
                self.config.pop("admitted_at", None)
        return self._call(method, params, abandoned=abandoned, preparing=preparing)

    def _call(
        self, method, params=None, *, abandoned=None, preparing=None, admitted=False
    ):
        asked = params or {}
        if asked.get("server") in self.remote_servers():
            if method in {"invoke", "mcp"}:
                return self.remote_mcp(method, asked)
        operation_deadline = time.monotonic() + 660
        if self.config.get("lease_path") and (
            method in {"invoke", "mcp", "take"}
            # Starting a command acquires hands; reading one that runs does not.
            or (
                method == "control"
                and (params or {}).get("subtype") == "shell"
                and (params or {}).get("operation") == "start"
            )
            # A project's hooks run on the machine that holds the project, and
            # its files are there (pi's tools take them one at a time).
            or (
                method == "control"
                and (params or {}).get("subtype") in ("tool_hooks", "files")
            )
        ):
            # Only a requested execution operation acquires hands. Bootstrap,
            # context discovery and a platform-only tool never enter this path.
            # A session started before its machine was rented sees the project
            # at the placeholder until it is relaunched there (`client.prepare`);
            # its paths are the machine's once they leave it.
            original_workspace = self.config.setdefault(
                "virtual_workspace", self.config["workspace"]
            )
            # A start on an admission just taken does not take the hands again
            # (`call`); its paths are still spelled the machine's way below.
            changed_lease = not admitted and self.acquire(
                deadline=operation_deadline, abandoned=abandoned, preparing=preparing
            )
            workspace = self.config["workspace"]
            if original_workspace != workspace or self.config.get("checkout_pending"):
                # The session started somewhere else — before its machine was
                # rented — or could not ask for its checkout then; its short
                # name is spelled the machine's way here, into its task's
                # checkout when it works one (`working_directory`).
                workspace = self.working_directory()
            elif (working := self.config.get("working_directory")) and (
                working != workspace
            ):
                # The session works somewhere else than the project view (a
                # task's checkout), and names paths from there: spell the view's
                # own path into it for every call, not only the one that
                # resolved. A room's session has the two equal.
                original_workspace, workspace = workspace, working
            if params and method == "invoke":
                params = {**params, "args": dict(params.get("args", {}))}
                for field in ("file_path", "path", "notebook_path", "command"):
                    value = params["args"].get(field)
                    if isinstance(value, str):
                        params["args"][field] = to_machine(
                            value, original_workspace, workspace
                        )
            if params and method == "control":
                params = dict(params)
                for field in ("body", "cwd", "path"):
                    if isinstance(params.get(field), str):
                        params[field] = to_machine(
                            params[field], original_workspace, workspace
                        )
            target_file = self.config.get("target_file")
            if target_file and changed_lease:
                tree = self.call("context_fs", {"operation": "tree"})
                if tree.get("unsupported_imports") or tree.get("unsupported_paths"):
                    raise RuntimeError(
                        "Project context leaves the forwarded project boundary"
                    )
                _replace_file(
                    Path(target_file).with_name("context-tree.json"), json.dumps(tree)
                )
                hooks_changed = register_project_hooks(
                    self.config, tree.get("hooks") or {}
                )
                context = self.call("context", {"known_files": {}})
                if context.get("instructions") or hooks_changed:
                    # No project operation has run yet. The caller reads the
                    # repository's instructions, and the build takes up its
                    # hooks, before the operation is tried: this one passed
                    # its PreToolUse before either was known.
                    raise RuntimeError(
                        "Work environment is ready. "
                        "The requested operation has not run. "
                        + (
                            "Apply these repository instructions "
                            "before issuing the next tool:\n" + context["instructions"]
                            if context.get("instructions")
                            else "The project's tool hooks now apply; issue it again."
                        )
                    )
        if method == "take":
            # Only the hands: whoever asked (pi's `Machine.take`) reads the
            # project's instructions itself.
            return {}
        if self.config.get("kind") == "deferred":
            operation = (params or {}).get("operation")
            if method == "context_fs" and operation == "tree":
                return {"generation": "no-work-lease", "entries": {}}
            # The placeholder holds no project yet, so nothing is at any path
            # in it. Said as an error instead, Claude Code starting there drops
            # every skill in its config directory.
            if method == "context_fs" and operation == "directory":
                return {"missing": True}
            if method == "context_fs" and operation == "list":
                return {"directories": []}
            if method == "ping":
                return {"workspace": self.config["workspace"], "mcp_servers": []}
            raise MachineOutOfReach
        if self.config.get("kind") == "unavailable":
            raise MachineOutOfReach
        if (
            method in {"invoke", "mcp"}
            and params
            and params.get("server") in self.command_servers()
        ):
            params = {**params, "spec": self.config["mcp_commands"][params["server"]]}
        if method in {"invoke", "mcp"} and params and self.local(params.get("server")):
            params = {**params, **self._local_start(params["server"])}
        if self.config.get("kind") == "device":
            deadline = min(
                operation_deadline, time.monotonic() + CONNECT_RETRY_WINDOW_S
            )
            attempt = 0
            while True:
                remaining = operation_deadline - time.monotonic()
                if remaining <= 0:
                    raise MachineOutOfReach
                payload = json.dumps(
                    {"method": method, "params": params or {}, "timeout": remaining}
                ).encode()
                connection, path = self.connection()
                connection.timeout = remaining
                if connection.sock:
                    connection.sock.settimeout(remaining)
                try:
                    connection.request(
                        "POST",
                        path,
                        body=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.execution_token()}",
                            **self.transport.headers,
                        },
                    )
                    response = connection.getresponse()
                    data = response.read()
                    if response.status == 503 and response.getheader(
                        OWNER_DRAINING_HEADER
                    ):
                        connection.close()
                        self.transport.connection = None
                        if not _retry_connect(attempt, deadline):
                            raise MachineOutOfReach
                        attempt += 1
                        continue
                    if response.status != 200:
                        # agent 读到的那句话里没有状态码，平台这边一个都不少：
                        # 少了这一行，后端事后连「当时是哪个码」都查不出来。
                        logger.warning(
                            "executor %s -> %s: %s", method, response.status, data[:200]
                        )
                        if _link_interrupted(response):
                            raise RuntimeError(LINK_INTERRUPTED)
                        if (
                            response.status in OUT_OF_REACH_STATUSES
                            or _device_is_offline(response)
                        ):
                            raise MachineOutOfReach
                        if response.status == 403:
                            token = self.execution_token()
                            if keeps_nothing(token):
                                raise RuntimeError(SCRATCH_REFUSED)
                            if _claimed(token, "ro"):
                                raise RuntimeError(READ_ONLY_REFUSED)
                        raise RuntimeError(EXECUTOR_CALL_FAILED)
                    return json.loads(data)
                except ConnectionRefusedError as exc:
                    # Nothing was sent, so this is the one failure worth waiting
                    # out: the platform endpoint is being replaced. Once the
                    # window is spent, nobody is listening — that IS out of
                    # reach, and saying so is what stops the rest of the turn
                    # from queueing up behind the same wait.
                    connection.close()
                    self.transport.connection = None
                    if not _retry_connect(attempt, deadline):
                        raise MachineOutOfReach from exc
                    attempt += 1
                except TimeoutError as exc:
                    # 读超时那一档（连接的 660 秒）：执行器一个字也没答。这不是
                    # 「某次调用失败了」，是这台机器这一刻够不着 —— 而认出它来，正是
                    # 为了让这一轮余下的文件与命令调用不必各自再等一次 660 秒。
                    #
                    # 只有这一种。**连接被重置不在内**：那次请求已经发出去了，执行
                    # 器很可能已经把那次改动做完了，丢的只是应答 —— 一台答得出话的
                    # 机器不叫够不着，把它也说成够不着，接下来一整段时间里每一次工
                    # 具调用都会被一次丢包当掉。
                    connection.close()
                    self.transport.connection = None
                    raise MachineOutOfReach from exc
                except Exception:
                    # A lost response can follow a committed mutation. Reconnect
                    # only for the next call; never replay this one.
                    connection.close()
                    self.transport.connection = None
                    raise
        result = subprocess.run(
            self.command("request"),
            input=json.dumps({"method": method, "params": params or {}}),
            text=True,
            capture_output=True,
            timeout=660,
        )
        if result.returncode:
            raise RuntimeError(
                "Remote executor request failed: " + result.stderr.strip()
            )
        return json.loads(result.stdout)

    def control(self, request, preparing=None):
        return self.call("control", dict(request), preparing=preparing)

    def checkpoint(self, request_id, turn=None):
        """A turn's Stop checkpoint: its work synced into the project. A
        session whose work is not kept (a 支线) has none
        to sync, and the executor route would refuse it. Nor does a session
        that never held a sandbox: its turn did nothing on a machine. (One
        whose sandbox was destroyed while idle is the route's to answer.)

        `turn` is the work id of the turn that ended. The machine runs its
        sync with it (``CHEESE_TURN``), so a push failure is filed under that
        turn instead of being guessed from whatever is newest later."""
        if self.config.get("kind") == "deferred" or keeps_nothing(
            self.execution_token()
        ):
            return {}
        request = {"subtype": "checkpoint", "request_id": request_id}
        if turn:
            request["turn"] = str(turn)
        return self.control(request)
