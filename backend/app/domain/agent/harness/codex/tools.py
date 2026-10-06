"""Expose the room executor's tool schemas and receipts to app-server."""

import asyncio
import base64
import contextlib
import importlib.resources
import io
import json
import posixpath
import shutil
import time
import types
from pathlib import Path

from app.domain.agent.executor_transport import (
    DEFERRED_WORKSPACE,
    MACHINE_OUT_OF_REACH,
    MachineOutOfReach,
    PlatformHost,
    RemoteClient,
    session_path,
)
from app.domain.agent.harness.driven.runner import reply_owed

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

#: Where Codex finds a repository's skills, relative to the project root: the
#: `.agents/skills` directory and the `skills` folder of the project's `.codex`
#: config layer (`codex-rs/ext/skills/src/host_roots.rs` at rust-v0.154.0).
SKILL_ROOTS = (".agents/skills", ".codex/skills")

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
    exec(compile(source, "cheese", "exec"), module.__dict__)  # noqa: S102
    return module


class RemoteTools:
    def __init__(
        self,
        target: dict,
        mirror: Path | None = None,
        reply_file: Path | None = None,
        shipped: Path | None = None,
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
        changed = False
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
        """Every tool the session lists: the executor's own, each MCP server's
        the session reaches (`RemoteClient.session_servers`), and the
        platform's.

        A session started on its leased machine (`central_provider`) takes it
        first, as Claude Code's does (`_take_leased_machine`): the lease names
        the machine's stdio servers, the checkout's `.mcp.json` ones, which the
        target the session was started with does not. Taken as it is, with no
        wait for a machine being prepared; a lease the platform cannot hand out
        fails the start."""
        target = self.client.config
        if (
            target.get("kind") == "deferred"
            and target.get("workspace") != DEFERRED_WORKSPACE
        ):
            await asyncio.to_thread(self.client.acquire, deadline=time.monotonic())
        tools = []
        routes = {}
        for server in ["native", *self.client.session_servers()]:
            cursor = None
            while True:
                result = await asyncio.to_thread(
                    self.client.call,
                    "mcp",
                    {
                        "server": server,
                        "method": "tools/list",
                        "params": {"cursor": cursor} if cursor else {},
                    },
                )
                for tool in result["tools"]:
                    original = tool["name"]
                    if server == "native" and original not in NATIVE_TOOLS:
                        continue
                    name = (
                        original if server == "native" else f"mcp__{server}__{original}"
                    )
                    if name in routes:
                        raise ValueError(f"Duplicate executor tool: {name}")
                    routes[name] = (server, original)
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
        for tool in self.platform.PLATFORM_TOOLS.schemas():
            if tool["name"] in routes:
                raise ValueError(f"Duplicate executor tool: {tool['name']}")
            routes[tool["name"]] = (PLATFORM, tool["name"])
            tools.append({"type": "function", **tool})
        self.routes = routes
        return tools

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
        self, tool: str, call_id: str, arguments: dict, notice: io.StringIO
    ) -> dict:
        def invoke(payload, args):
            return self.client.call(
                "invoke",
                {"id": payload["id"], "tool": payload["tool"], "args": args},
                preparing=notice,
            )

        host = PlatformHost(self.client, invoke, call_id, self.doc_versions)
        try:
            text = self.platform.run_platform_tool(tool, arguments, host)
        except MachineOutOfReach:
            text, success = MACHINE_OUT_OF_REACH, False
        except Exception as exc:  # noqa: BLE001 — the agent reads the reason
            text, success = str(exc), False
        else:
            success = True
        return {
            "success": success,
            "contentItems": [{"type": "inputText", "text": text}],
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
                if params["tool"] in owed["answers"]:
                    self.answered = owed["id"]
                    # And where the runner reads it, to know the turn may end.
                    Path(owed["answered"]).write_text(owed["id"])
                elif params["tool"] not in owed["reads"]:
                    return {
                        "success": False,
                        "contentItems": [{"type": "inputText", "text": owed["reason"]}],
                    }
        server, tool = self.routes[params["tool"]]
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

    async def _executor_call(
        self,
        server: str,
        tool: str,
        params: dict,
        notice: io.StringIO,
        session_call: bool,
    ) -> dict:
        receipt: dict
        if server == "native":
            await self.find_shipped(params["arguments"])
            params = {**params, "arguments": self.on_the_machine(params["arguments"])}
        if server in self.client.remote_servers():
            # Codex fires no project hooks, and a remote server's call never
            # reaches the machine that would run them around it.
            try:
                receipt = await asyncio.to_thread(
                    self.client.remote_call_with_hooks,
                    params["callId"],
                    server,
                    tool,
                    params["arguments"],
                    notice,
                )
            except MachineOutOfReach:
                receipt = {"error": MACHINE_OUT_OF_REACH}
        else:
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
            if session_call and server == "native" and tool == "Bash":
                await self._yield_when_spoken_to(params["callId"], invoked)
            receipt = await invoked
        return receipt

    @staticmethod
    def _answer(server: str, receipt: dict) -> dict:
        if "error" in receipt:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": receipt["error"]}],
            }
        value = receipt["value"]
        if server == "native":
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
        content = []
        for item in value.get("content", []):
            if item["type"] == "text":
                content.append({"type": "inputText", "text": item["text"]})
            elif item["type"] in ("image", "audio"):
                image = item["type"] == "image"
                url = f"data:{item['mimeType']};base64,{item['data']}"
                content.append(
                    {
                        "type": "inputImage" if image else "inputAudio",
                        "imageUrl" if image else "audioUrl": url,
                    }
                )
            else:
                # Preserve structured resources that have no app-server content type.
                content.append({"type": "inputText", "text": json.dumps(item)})
        if value.get("structuredContent") is not None:
            content.append(
                {"type": "inputText", "text": json.dumps(value["structuredContent"])}
            )
        return {"success": not value.get("isError", False), "contentItems": content}
