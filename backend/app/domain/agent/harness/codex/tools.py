"""Expose the room executor's tool schemas and receipts to app-server."""

import asyncio
import base64
import contextlib
import importlib.resources
import io
import json
import posixpath
import re
import shutil
import types
from collections.abc import Callable
from pathlib import Path

from app.domain.agent.executor_transport import (
    DEFERRED_WORKSPACE,
    MACHINE_OUT_OF_REACH,
    MachineOutOfReach,
    PlatformHost,
    RemoteClient,
    failure_text,
    no_run,
    session_path,
)
from app.domain.agent.harness.driven import monitor
from app.domain.agent.harness.driven.mcp_gateway import (
    SECRET_ENV,
    SECRET_HEADER,
    STARTUP_S,
    Gateway,
)
from app.domain.agent.harness.driven.runner import reply_owed
from app.domain.agent.pr_creation import REFUSAL, opens_pull_request

# The executor implements these tools. The platform's own tools are not the
# executor's to list: they are the constant table (`platform_tools`).
NATIVE_TOOLS = {
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Glob",
    "Grep",
    "NotebookEdit",
    "TaskOutput",
    "TaskStop",
}


#: How often a running Bash looks for a person having written.
YIELD_POLL_S = 0.2

#: The route a platform tool takes: not a server on the executor, the backend.
PLATFORM = "platform"
#: The server names Codex takes (`mcp_config`).
NAME = re.compile(r"[A-Za-z0-9_-]+")
#: How long Codex waits on one project tool call: longer than the executor
#: waits for the room's machine (`RemoteClient.call`'s deadline).
TOOL_TIMEOUT_S = 900

#: The route `Monitor` takes: this process, which follows the command on the
#: machine and tells the session what it prints (`driven/monitor.py`).
MONITOR = "monitor"

#: Where Codex finds a repository's skills, relative to the project root: the
#: `.agents/skills` directory and the `skills` folder of the project's `.codex`
#: config layer (`codex-rs/ext/skills/src/host_roots.rs` at rust-v0.154.0).
SKILL_ROOTS = (".agents/skills", ".codex/skills")

#: The native project instruction files Codex discovers from its cwd.
PROJECT_DOC_NAMES = ("AGENTS.override.md", "AGENTS.md")

#: The arguments of an executor tool that name a path or run a command.
PATH_ARGUMENTS = ("file_path", "path", "notebook_path", "command")

#: How much of one skill file a single context read asks the machine for.
SKILL_READ_BYTES = 1024 * 1024


def platform_tools():
    """The platform's tool table and its runner (结论 63): the one file every
    harness serves them from. Shipped beside this module in the runner archive
    (``bundle.py``); read from the source tree when running from a checkout."""
    shipped = importlib.resources.files(__package__).joinpath("cheese.py")
    source = (
        shipped.read_text()
        if shipped.is_file()
        else (Path(__file__).resolve().parents[5] / "sandbox" / "cheese").read_text()
    )
    module = types.ModuleType("cheese_platform_tools")
    exec(compile(source, "cheese", "exec"), module.__dict__)
    return module


class RemoteTools:
    def __init__(
        self,
        target: dict,
        mirror: Path | None = None,
        reply_file: Path | None = None,
        shipped: Path | None = None,
        project_docs: Path | None = None,
    ):
        self.client = RemoteClient(target)
        self.routes: dict[str, tuple[str, str]] = {}
        self.platform = platform_tools()
        self.doc_versions: dict[str, int] = {}
        # The project's skills, where the session's Codex can read them. It
        # has no environment of its own (`session.py`), so it finds none in
        # the project; it finds these as extra roots (`skill_roots`).
        self.mirror = mirror
        # The platform's own skills (its own and the ways of working the
        # project saved), written here by the runner. The room's executor
        # holds the same files in its config dir, where the launch planted
        # them (`bootstrap.plant_native_skills`); `executor_config` is that
        # directory, asked of the executor once.
        self.shipped = shipped
        self.executor_config: str | None = None
        # Codex's cwd is a project root, so mirror project instructions there
        # at their original relative paths for native instruction discovery.
        self.project_docs = project_docs
        self.docs_mirrored: dict[str, tuple[str, int, int]] | None = None
        # What the mirror holds: its path -> the (size, mtime) it was read at.
        self.mirrored: dict[str, tuple[int, int]] | None = None
        # Where the runner says a person is waiting on an answer
        # (`driven/runner.py`), the thread that owes it — a subagent's thread
        # reports to its parent, not to the room — and the debt already
        # answered: a reply and the next call can come in one step, and the
        # reply's call arrives here first.
        self.reply_file = reply_file
        self.main_thread: str | None = None
        self.answered: str | None = None
        # The session's monitors, once the runner that tells the session what
        # they print exists (`entry.py`).
        self.monitors: monitor.Monitors | None = None
        # How to ask the session's own thread to compact itself, for the one
        # tool that does (`cheese_session(action=compact)`): the runner's, set beside
        # `monitors` (`entry.py`). A session without one refuses the call.
        self.compact: Callable[[], str] | None = None
        # The project's MCP servers the session opened with (`discover`).
        self.mcp_servers: list[str] = []

    def skill_roots(self) -> list[str]:
        assert self.mirror is not None
        roots = [str(self.mirror / root) for root in SKILL_ROOTS]
        if self.shipped is not None and (self.shipped / "skills").is_dir():
            # Beside the project's: plain Codex lists a user skill and a
            # repository skill of the same name side by side, and so does this.
            roots.append(str(self.shipped / "skills"))
        return roots

    def ship_skills(self, files: dict[str, str]) -> None:
        """Write the platform's skills (`skills/<name>/...`) where the
        session's Codex reads them, replacing what an earlier process wrote."""
        assert self.shipped is not None
        shutil.rmtree(self.shipped, ignore_errors=True)
        for name, content in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"A platform skill file outside its folder: {name}")
            path = self.shipped / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    async def find_shipped(self, arguments: dict) -> None:
        """Where the executor holds the platform's skills, asked the first
        time a call names one of them: the machine is being reached anyway
        then, and a session that never does never waits on it."""
        if self.shipped is None or self.executor_config is not None:
            return
        here = str(self.shipped / "skills")
        if any(
            isinstance(value, str) and here in value
            for key, value in arguments.items()
            if key in PATH_ARGUMENTS
        ):
            status = await asyncio.to_thread(self.client.call, "ping")
            self.executor_config = status.get("config_dir")

    def sync_project_docs(self, entries: dict[str, dict]) -> bool:
        """Mirror AGENTS.md files into Codex's cwd without interpreting them.

        Codex owns discovery and its 32 KiB combined instruction limit. The
        executor supplies the same relative paths and bytes, so root and nested files
        retain Codex's normal precedence and scope.
        """
        if self.project_docs is None:
            return False
        self.project_docs.mkdir(parents=True, exist_ok=True)
        if self.docs_mirrored is None:
            # A runner restart gets a fresh in-memory index. Clear only the
            # native document names so a previous checkout cannot leak rules.
            for path in self.project_docs.rglob("*"):
                if path.is_file() and path.name in PROJECT_DOC_NAMES:
                    path.unlink()
            self.docs_mirrored = {}

        def source_file(name: str) -> tuple[str, dict] | None:
            source = name
            seen = set()
            while source not in seen:
                seen.add(source)
                parts = source.split("/")
                for index in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:index])
                    link = entries.get(prefix, {})
                    if link.get("kind") != "symlink":
                        continue
                    target = link.get("target")
                    if not isinstance(target, str) or target.startswith("/"):
                        return None
                    source = posixpath.normpath(
                        posixpath.join(
                            posixpath.dirname(prefix), target, *parts[index:]
                        )
                    )
                    if source == ".." or source.startswith(("../", "/")):
                        return None
                    break
                else:
                    entry = entries.get(source, {})
                    return (source, entry) if entry.get("kind") == "file" else None
            return None

        wanted: dict[str, tuple[str, int, int]] = {}
        for name, entry in entries.items():
            if (
                entry.get("kind") not in {"file", "symlink"}
                or posixpath.basename(name) not in PROJECT_DOC_NAMES
                or name.startswith("/")
                or posixpath.normpath(name) != name
                or name == ".."
                or name.startswith("../")
            ):
                continue
            resolved = source_file(name)
            if resolved is not None:
                source, source_entry = resolved
                wanted[name] = (
                    source,
                    source_entry["size"],
                    source_entry["mtime_ns"],
                )

        changed = False
        for name in [name for name in self.docs_mirrored if name not in wanted]:
            (self.project_docs / name).unlink(missing_ok=True)
            del self.docs_mirrored[name]
            changed = True

        for name, stamp in wanted.items():
            if self.docs_mirrored.get(name) == stamp:
                continue
            data = b""
            while len(data) < stamp[1]:
                piece = base64.b64decode(
                    self.client.call(
                        "context_fs",
                        {
                            "operation": "read",
                            "path": stamp[0],
                            "offset": len(data),
                            "size": min(SKILL_READ_BYTES, stamp[1] - len(data)),
                        },
                    )["data"]
                )
                if not piece:
                    break
                data += piece

            path = self.project_docs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.docs_mirrored[name] = stamp
            changed = True
        return changed

    def sync_skills(self) -> bool:
        """The mirror holds the project's skills as the machine has them now.

        Plain Codex scans the project's skill roots itself and watches them.
        A room's Codex reads them from the mirror, which this brings up to
        date from the executor's context tree before every turn. Whether
        anything changed.
        """
        assert self.mirror is not None
        if self.mirrored is None:
            # A mirror left by an earlier process may hold what the project
            # has since dropped.
            shutil.rmtree(self.mirror, ignore_errors=True)
            self.mirrored = {}
        entries = self.client.call("context_fs", {"operation": "tree"}).get(
            "entries", {}
        )
        changed = self.sync_project_docs(entries)
        wanted: dict[str, str] = {}

        def collect(held: str, source: str, depth: int = 0) -> None:
            # `held` is where the project shows `source`; a link to a
            # directory elsewhere in the project shows that directory's files
            # at the link's own path, as reading through it on the machine does.
            for name, entry in entries.items():
                if name != source and not name.startswith(source + "/"):
                    continue
                place = held + name[len(source) :]
                if entry["kind"] == "file":
                    wanted[place] = name
                elif entry["kind"] == "symlink" and depth < 8:
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), entry["target"])
                    )
                    if target not in (".", "") and not target.startswith(("..", "/")):
                        collect(place, target, depth + 1)

        for root in SKILL_ROOTS:
            collect(root, root)
        for place in [place for place in self.mirrored if place not in wanted]:
            (self.mirror / place).unlink(missing_ok=True)
            del self.mirrored[place]
            changed = True
        for place, name in wanted.items():
            entry = entries[name]
            stamp = (entry["size"], entry["mtime_ns"])
            if self.mirrored.get(place) == stamp:
                continue
            data = b""
            while len(data) < entry["size"]:
                piece = base64.b64decode(
                    self.client.call(
                        "context_fs",
                        {
                            "operation": "read",
                            "path": name,
                            "offset": len(data),
                            "size": min(SKILL_READ_BYTES, entry["size"] - len(data)),
                        },
                    )["data"]
                )
                if not piece:
                    break
                data += piece
            path = self.mirror / place
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.mirrored[place] = stamp
            changed = True
        for directory in sorted(self.mirror.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return changed

    def on_the_machine(self, arguments: dict) -> dict:
        """`arguments` with each path in the mirror named where the machine
        holds it: the model reads a skill's files, and runs its scripts, at
        the paths the skill list gave it, which are the mirror's."""
        places = []
        workspace = self.client.config.get("workspace")
        if self.mirror is not None and workspace:
            places.append((str(self.mirror), session_path(workspace)))
        if self.shipped is not None and self.executor_config:
            places.append(
                (str(self.shipped / "skills"), self.executor_config + "/skills")
            )
        if not places:
            return arguments

        def placed(value: str) -> str:
            for here, there in places:
                value = value.replace(here, there)
            return value

        return {
            key: placed(value)
            if key in PATH_ARGUMENTS and isinstance(value, str)
            else value
            for key, value in arguments.items()
        }

    async def discover(self) -> list[dict]:
        """Every tool the session lists besides the project's MCP servers'
        (Codex's own client reaches those, at the gateway — `mcp_config`): the
        executor's own, and the platform's.

        A session started on its leased machine (`central_provider`) takes it
        first, as Claude Code's does (`_take_leased_machine`): the lease names
        the machine's stdio servers, the checkout's `.mcp.json` ones, which the
        target the session was started with does not. Taken as it is, with no
        wait for a machine being prepared: a lease the platform cannot hand out
        now leaves the session at the placeholder, as Claude Code's starts
        there, listing no machine server until it is relaunched onto it."""
        target = self.client.config
        if (
            target.get("kind") == "deferred"
            and target.get("workspace") != DEFERRED_WORKSPACE
            and not await asyncio.to_thread(self.client.take_now)
        ):
            target["workspace"] = DEFERRED_WORKSPACE
        # The servers the session opens with, as they are before its own tools
        # are listed: listing those may take the machine, which names the
        # checkout's servers, and a session at the placeholder lists none.
        self.mcp_servers = self.client.session_servers()
        tools = []
        routes = {}
        cursor = None
        while True:
            result = await asyncio.to_thread(
                self.client.call,
                "mcp",
                {
                    "server": "native",
                    "method": "tools/list",
                    "params": {"cursor": cursor} if cursor else {},
                },
            )
            for tool in result["tools"]:
                name = tool["name"]
                if name not in NATIVE_TOOLS:
                    continue
                routes[name] = ("native", name)
                tools.append(
                    {
                        "type": "function",
                        "name": name,
                        "description": tool.get("description", ""),
                        "inputSchema": tool["inputSchema"],
                    }
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        for tool in self.platform.offered_tools():
            if tool["name"] in routes:
                raise ValueError(f"Duplicate executor tool: {tool['name']}")
            routes[tool["name"]] = (PLATFORM, tool["name"])
            tools.append({"type": "function", **tool})
        if self.monitors is not None:
            if monitor.TOOL["name"] in routes:
                # i18n-exempt: runner bundle: session host, stdlib only, no catalog
                raise ValueError(f"Duplicate executor tool: {monitor.TOOL['name']}")
            routes[monitor.TOOL["name"]] = (MONITOR, monitor.TOOL["name"])
            tools.append({"type": "function", **monitor.TOOL})
        self.routes = routes
        return tools

    def gateway(self) -> Gateway:
        """The door Codex's own MCP client reaches the project's servers by."""
        return Gateway(
            self.client.call,
            self.client.session_servers,
            tool_call=self.mcp_tool_call,
            observe=self.client.local_outcome,
            on_close=self.client.local_closed,
        )

    def mcp_tool_call(self, params: dict) -> dict:
        """A project server's tool call, as the gateway hands it on. Codex
        fires no project hooks of its own — every other tool it has is the
        platform's, which the machine runs them around — so they are run
        around this one too: by the machine for its own commands, and here,
        on it, for a remote server, whose call never reaches it."""
        if params["server"] in self.client.remote_servers():
            try:
                return self.client.remote_call_with_hooks(
                    params["id"], params["server"], params["tool"], params["args"]
                )
            except MachineOutOfReach:
                return {"error": MACHINE_OUT_OF_REACH}
        return self.client.call("invoke", params)

    async def _yield_when_spoken_to(
        self, call_id: str, invoked: asyncio.Future
    ) -> None:
        """While the session's Bash runs, watch for a person writing.

        A message reaches Codex only between tool calls, so one written during
        a long command waited for it to end. When the runner writes down a new
        debt (`driven/runner.py`), the executor stops waiting on this call's
        command and returns it as a background task that goes on running
        (`runtime.bash`) — Claude Code's Ctrl+B, for a Bash that is the
        executor's."""
        while not invoked.done():
            await asyncio.wait({invoked}, timeout=YIELD_POLL_S)
            owed = reply_owed(self.reply_file)
            # Unanswered: no tool starts while one is (`__call__`), so this
            # command was already running when the person wrote.
            if not invoked.done() and owed is not None and owed["id"] != self.answered:
                # An executor that cannot do this leaves the command waiting,
                # which is what it did before; the call itself must not fail.
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        self.client.call,
                        "control",
                        {"subtype": "background", "id": call_id},
                    )
                return

    def _platform_call(
        self,
        tool: str,
        call_id: str,
        arguments: dict,
        notice: io.StringIO,
        session_call: bool,
    ) -> dict:
        def invoke(payload, args):
            return self.client.call(
                "invoke",
                {"id": payload["id"], "tool": payload["tool"], "args": args},
                preparing=notice,
            )

        # A subagent's thread is not the room's session: compacting it would
        # compact the wrong context, and it is not the one the room reads.
        host = PlatformHost(
            self.client,
            invoke,
            call_id,
            self.doc_versions,
            no_run,
            self.compact if session_call else None,
        )
        try:
            text = self.platform.run_platform_tool(tool, arguments, host)
        except MachineOutOfReach:
            text, success = MACHINE_OUT_OF_REACH, False
        except Exception as exc:  # the agent reads the reason
            text, success = failure_text(exc), False
        else:
            success = True
        # Pictures a tool hands back (a mini-app's, as the platform ran it)
        # follow its text.
        images = [
            {
                "type": "inputImage",
                "imageUrl": f"data:{i['mimeType']};base64,{i['data']}",
            }
            for i in getattr(text, "images", ())
        ]
        return {
            "success": success,
            "contentItems": [{"type": "inputText", "text": str(text)}, *images],
        }

    async def __call__(self, method: str, params: dict) -> dict:
        if method != "item/tool/call":
            raise ValueError(f"Unsupported Codex server request: {method}")
        session_call = self.main_thread in (None, params.get("threadId"))
        if session_call:
            owed = reply_owed(self.reply_file)
            if owed is not None and owed["id"] != self.answered:
                # Reading the room is on the way to answering it, and answers
                # nothing.
                arguments = params.get("arguments")
                action = (
                    arguments.get("action") if isinstance(arguments, dict) else None
                )
                # A read is one verb of an object (`cheese_chat.list`).
                key = f"{params['tool']}.{action}" if action else params["tool"]
                if params["tool"] in owed["answers"] or key in owed["answers"]:
                    self.answered = owed["id"]
                    # And where the runner reads it, to know the turn may end.
                    Path(owed["answered"]).write_text(owed["id"])
                elif key not in owed["reads"]:
                    return {
                        "success": False,
                        "contentItems": [{"type": "inputText", "text": owed["reason"]}],
                    }
        server, tool = self.routes[params["tool"]]
        if server == MONITOR or self._stops_a_monitor(server, tool, params):
            return await self._monitor_call(tool, params, session_call)
        # What the platform said while this call waited for its machine. Codex
        # hears a call only once it has run, so the agent reads it at the head
        # of the call's result.
        notice = io.StringIO()
        if server == PLATFORM:
            answer = await asyncio.to_thread(
                self._platform_call,
                tool,
                params["callId"],
                params["arguments"],
                notice,
                session_call,
            )
        else:
            receipt = await self._executor_call(
                server, tool, params, notice, session_call
            )
            answer = self._answer(server, receipt)
        if notice.getvalue():
            answer["contentItems"] = [
                {"type": "inputText", "text": notice.getvalue().strip()},
                *answer["contentItems"],
            ]
        return answer

    def _stops_a_monitor(self, server: str, tool: str, params: dict) -> bool:
        """`TaskStop` names a monitor as it names a background command: by
        the task id it was started under."""
        arguments = params.get("arguments") or {}
        task = arguments.get("task_id") or arguments.get("shell_id")
        return (
            server == "native"
            and tool == "TaskStop"
            and isinstance(task, str)
            and task.startswith(monitor.PREFIX)
        )

    async def _monitor_call(self, tool: str, params: dict, session_call: bool) -> dict:
        assert self.monitors is not None
        arguments = params.get("arguments") or {}
        if tool == "TaskStop":
            # Routed here only for a monitor id (`_is_monitor_call`).
            task = str(arguments.get("task_id") or arguments.get("shell_id") or "")
            text, success = await self.monitors.stop(task), True
        elif not session_call:
            # What a monitor prints is told to the room's session; a subagent
            # would never hear it.
            text, success = "Monitor is not available to a subagent", False
        else:
            try:
                text = await self.monitors.start(arguments, params["callId"])
                success = True
            except MachineOutOfReach:
                text, success = MACHINE_OUT_OF_REACH, False
            except Exception as exc:  # the agent reads the reason
                text, success = failure_text(exc), False
        return {
            "success": success,
            "contentItems": [{"type": "inputText", "text": text}],
        }

    async def _executor_call(
        self,
        server: str,
        tool: str,
        params: dict,
        notice: io.StringIO,
        session_call: bool,
    ) -> dict:
        # The platform opens the task's PR (`pr_creation`).
        if (
            server == "native"
            and tool == "Bash"
            and opens_pull_request(str(params["arguments"].get("command") or ""))
        ):
            return {"error": REFUSAL}
        await self.find_shipped(params["arguments"])
        params = {**params, "arguments": self.on_the_machine(params["arguments"])}
        invoked = asyncio.ensure_future(
            asyncio.to_thread(
                self.client.call,
                "invoke",
                {
                    "id": params["callId"],
                    "server": server,
                    "tool": tool,
                    "args": params["arguments"],
                },
                preparing=notice,
            )
        )
        if session_call and tool == "Bash":
            await self._yield_when_spoken_to(params["callId"], invoked)
        return await invoked

    @staticmethod
    def _answer(server: str, receipt: dict) -> dict:
        """What one of the executor's own tools answered, as Codex reads it."""
        if "error" in receipt:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": receipt["error"]}],
            }
        value = receipt["value"]
        if isinstance(value, dict) and value.get("type") == "image":
            file = value["file"]
            return {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputImage",
                        "imageUrl": f"data:{file['type']};base64,{file['base64']}",
                    }
                ],
            }
        return {
            "success": True,
            "contentItems": [
                {"type": "inputText", "text": json.dumps(value, ensure_ascii=False)}
            ],
        }


def mcp_config(servers: list[str], port: int) -> list[str]:
    """The project's servers as Codex's own MCP client reaches them: each one
    at the gateway (`mcp_gateway`), as `app-server` overrides. The secret is
    read from the environment the app server is started with, not written
    here: a command line is read by every process on the shared machine."""
    overrides = []
    for name in servers:
        # Codex takes no other server name, and a dotted override no other key.
        if not NAME.fullmatch(name):
            continue
        key = f"mcp_servers.{name}"
        overrides += [
            "-c",
            f'{key}.url="http://127.0.0.1:{port}/mcp/{name}"',
            "-c",
            f'{key}.env_http_headers={{"{SECRET_HEADER}"="{SECRET_ENV}"}}',
            "-c",
            f"{key}.startup_timeout_sec={STARTUP_S}",
            # A call may wait for the room's machine to be prepared (`acquire`).
            "-c",
            f"{key}.tool_timeout_sec={TOOL_TIMEOUT_S}",
        ]
    return overrides
