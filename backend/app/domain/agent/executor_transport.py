"""Room executor transport shared by agent harnesses."""

import base64
import json
import logging
import os
import select
import shlex
import subprocess
import time
import uuid
from urllib.parse import unquote, urlsplit

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

# 够不着以外的那些。同样不给裸状态码（结论 23）：数字会把 agent 送回自己的工具调用
# 里找 bug。数字和响应体进的是进程日志 —— agent 读不到它们，平台读得到。
EXECUTOR_CALL_FAILED = "这次调用失败了，机器还在：其他工具照常可用，这一个可以重试。"


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


def _retry_connect(attempt: int, deadline: float) -> bool:
    """Sleep before the next attempt, or say the window is over.

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


class PlatformHost:
    """What a platform tool (`PLATFORM_TOOLS` in `sandbox/cheese`) runs against
    when the session is not on the machine (结论 63).

    The backend is reached from here, directly — that is why the table is whole
    while the machine is gone. The two things a tool can need from the machine
    (a file's bytes, a task's commits pushed) go through ``invoke`` and so share
    its breaker: gone means an immediate MACHINE_OUT_OF_REACH, never a wait.
    """

    def __init__(self, client, invoke, call_id, doc_versions):
        self.client = client
        self.invoke = invoke
        self.call_id = call_id
        self.doc_versions = doc_versions
        self.environ = dict(os.environ)

    def request(self, plan):
        stdout = self.client.platform_request(plan)["value"]["stdout"]
        return json.loads(stdout) if stdout else {}

    def read_file(self, path):
        # The agent spells paths as it was shown them: the workspace's own path,
        # or relative to it.
        workspace = session_path(self.client.config.get("workspace") or "")
        machine = path
        if workspace and not machine.startswith("/"):
            machine = f"{workspace.rstrip('/')}/{machine}"
        return read_file_on_the_machine(self.invoke, machine, f"{self.call_id}-read")

    def sync_task(self, task_id):
        # Pushing can include uploading a backup bundle; the CLI's own ceiling for
        # that upload is two minutes, the Bash tool's is ten.
        on_the_machine(
            self.invoke,
            "cheese sync --task " + shlex.quote(str(uuid.UUID(task_id))),
            f"{self.call_id}-sync",
            timeout_ms=600000,
        )


class RemoteClient:
    def __init__(self, config, *, shared_connection=False):
        import threading
        from types import SimpleNamespace

        self.config = config
        self.transport = SimpleNamespace() if shared_connection else threading.local()
        self.publication_lock = threading.Lock()
        self.publication = None
        self.platform_lock = threading.Lock()
        self.platform = None

    def execution_token(self):
        if self.config.get("execution_token"):
            return self.config["execution_token"]
        token_file = self.config.get("token_file")
        if token_file:
            with open(token_file) as stream:
                return stream.read().strip()
        return os.environ["CHEESE_TOKEN"]

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
        token = (
            self.execution_token()
            if self.config.get("token_file")
            else os.environ.get("CHEESE_TOKEN", "")
        )
        if not api or not token:
            raise RuntimeError("Platform requests require room credentials")
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
                        "X-Cheese-Token": token,
                        **(
                            {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                            if os.environ.get("CHEESE_TURN")
                            else {}
                        ),
                    },
                )
                response = connection.getresponse()
                body = response.read().decode()
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
        import http.client
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
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
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
                    "X-Cheese-Token": token,
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

    def command(self, mode, server=None):
        command = [*self.config["command"], mode, "--state", self.config["state"]]
        if server:
            command.extend(["--server", server])
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

    def call(self, method, params=None, *, abandoned=None):
        """``abandoned`` says the caller has given the operation up (a cancelled
        tool call). Acquiring hands can wait for a machine being prepared; an
        operation given up during that wait is never started."""
        operation_deadline = time.monotonic() + 660
        if self.config.get("lease_path") and (
            method in {"invoke", "mcp", "project_tools"}
            # Starting a command acquires hands; reading one that runs does not.
            or (
                method == "control"
                and (params or {}).get("subtype") == "shell"
                and (params or {}).get("operation") == "start"
            )
        ):
            # Only a requested execution operation acquires hands. Bootstrap,
            # context discovery and a platform-only tool never enter this path.
            while True:
                response = self.platform_request(
                    {
                        "method": "POST",
                        "path": self.config["lease_path"],
                        "body": {
                            "env": self.config.get("setup_env", {}),
                            "timeout": max(
                                0.001, operation_deadline - time.monotonic()
                            ),
                        },
                    }
                )
                result = json.loads(response["value"]["stdout"])["data"]
                if abandoned is not None and abandoned():
                    raise RuntimeError("Tool call was cancelled")
                # The platform waited as long as one request may while the
                # machine is prepared. Ask again until this operation's own
                # deadline, which is what bounds the wait.
                if not result.get("preparing") or time.monotonic() >= (
                    operation_deadline
                ):
                    break
            if result.get("preparing"):
                raise RuntimeError(f"{result['unavailable']}（等到操作时限仍未就绪）")
            if result.get("unavailable"):
                raise RuntimeError(result["unavailable"])
            original_workspace = self.config.setdefault(
                "virtual_workspace", self.config["workspace"]
            )
            target = result["target"]
            if params and method == "invoke":
                params = {**params, "args": dict(params.get("args", {}))}
                for field in ("file_path", "path", "notebook_path", "command"):
                    value = params["args"].get(field)
                    if isinstance(value, str):
                        params["args"][field] = value.replace(
                            original_workspace, target["workspace"]
                        )
            if params and method == "control":
                params = dict(params)
                for field in ("body", "cwd"):
                    if isinstance(params.get(field), str):
                        params[field] = params[field].replace(
                            original_workspace, target["workspace"]
                        )
            changed_lease = self.config.get("generation") != target.get("generation")
            self.config.update(target)
            token_file = self.config.get("token_file")
            if token_file:
                from pathlib import Path

                Path(token_file).write_text(result["token"])
                Path(token_file).chmod(0o600)
            else:
                self.config["execution_token"] = result["token"]
            target_file = self.config.get("target_file")
            if target_file:
                from pathlib import Path

                path = Path(target_file)
                temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
                temporary.write_text(json.dumps(self.config))
                temporary.chmod(0o600)
                temporary.replace(path)
            previous = getattr(self.transport, "connection", None)
            if previous is not None:
                previous.close()
                self.transport.connection = None
            if target_file and changed_lease:
                tree = self.call("context_fs", {"operation": "tree"})
                if tree.get("unsupported_imports") or tree.get("unsupported_paths"):
                    raise RuntimeError(
                        "Project context leaves the forwarded project boundary"
                    )
                tree_path = Path(target_file).with_name("context-tree.json")
                temporary = tree_path.with_name(tree_path.name + "." + uuid.uuid4().hex)
                temporary.write_text(json.dumps(tree))
                temporary.replace(tree_path)
                context = self.call("context", {"known_files": {}})
                if context.get("instructions"):
                    # No project operation has run yet. Let the caller read its
                    # newly available repository instructions before trying it.
                    raise RuntimeError(
                        "Work environment is ready. "
                        "The requested operation has not run. "
                        "Apply these repository instructions "
                        "before issuing the next tool:\n" + context["instructions"]
                    )
        if self.config.get("kind") == "deferred":
            if method == "context_fs" and (params or {}).get("operation") == "tree":
                return {"generation": "no-work-lease", "entries": {}}
            if method == "ping":
                return {"workspace": self.config["workspace"], "mcp_servers": []}
            raise MachineOutOfReach
        if self.config.get("kind") == "unavailable":
            raise MachineOutOfReach
        if method == "project_tools":
            params = params or {}
            server = params.get("server")
            if not server:
                return {"servers": self.config.get("mcp_servers", [])}
            if server not in self.config.get("mcp_servers", []):
                raise ValueError("Unknown project MCP server")
            tool = params.get("name")
            method = "mcp"
            params = {
                "server": server,
                "method": "tools/call" if tool else "tools/list",
                "params": {"name": tool, "arguments": params.get("arguments", {})}
                if tool
                else {},
                **(
                    {"id": params["id"], "tool": f"mcp__{server}__{tool}"}
                    if tool
                    else {}
                ),
            }
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
                            "X-Cheese-Token": self.execution_token(),
                            **self.transport.headers,
                        },
                    )
                    response = connection.getresponse()
                    data = response.read()
                    if response.status != 200:
                        # agent 读到的那句话里没有状态码，平台这边一个都不少：
                        # 少了这一行，后端事后连「当时是哪个码」都查不出来。
                        logger.warning(
                            "executor %s -> %s: %s", method, response.status, data[:200]
                        )
                        if (
                            response.status in OUT_OF_REACH_STATUSES
                            or _device_is_offline(response)
                        ):
                            raise MachineOutOfReach
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

    def control(self, request):
        return self.call("control", dict(request))
