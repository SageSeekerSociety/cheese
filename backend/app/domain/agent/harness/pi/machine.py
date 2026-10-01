"""pi 的手在房间的执行机上：文件和命令过去，会话留在中心机。

pi runs on the central session host, beside the room's other sessions, and the
project is on the room's machine. pi's own tools take their file and process
operations as injected ``Operations`` (``platform.ts``, #1106); each one
arrives at the runner as one request and leaves here as one command on the
machine, through the client every other harness's tools use
(``RemoteClient``). That client takes the room's machine the first time an
operation needs one and never before, so a question that touches no file never
waits for a machine.

A command here is the executor's own kind (``control`` ``shell``): started
under an id of ours, read from an offset, signalled, its output kept in files on
the machine. So a reader that loses its connection asks again from where it
was, and a command outlives the request that started it.

Standard library only: this runs in the runner archive on the session host.
"""

import base64
import importlib.resources
import json
import re
import shlex
import uuid
from pathlib import Path

from app.domain.agent.executor_transport import DEFERRED_WORKSPACE, RemoteClient

#: The most one file read brings back. pi reads a file whole and then cuts it to
#: what the model is shown; a file past this is refused rather than carried.
READ_LIMIT = 32 * 1024 * 1024

#: How long one read of a running command waits for more output: well under the
#: executor's own bound for a read (`COMMAND_READ_WAIT_S`), so an abort or a
#: timeout is noticed within it.
READ_WAIT_S = 2.0

#: What a file's first bytes say it is, for the image types pi hands a model
#: as an image rather than as text (pi's own read does the same sniffing).
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: The arguments of a call that name a path or a command, in the names a hook
#: reads them under (`hooks.shown`).
PATH_ARGUMENTS = ("file_path", "path", "notebook_path", "command")

#: What the session hears the first time it reaches the machine, when it was
#: started before there was one: the repository's own instructions are read
#: before anything is done in it, as on the other harnesses (`RemoteClient`).
WORK_READY = (
    "工作电脑已经就绪，这次操作没有执行。先读下面这个仓库自己的说明，"
    "再重新发起这次操作：\n\n"
)


#: The project's settings, as the executor reads them for its hooks
#: (`project_hooks.run`): each file that is there, parsed, or its text when it
#: does not parse.
SETTINGS = """import json, os, sys
found = []
for name in (".claude/settings.json", ".claude/settings.local.json"):
    path = os.path.join(sys.argv[1], name)
    if os.path.isfile(path):
        text = open(path, encoding="utf-8", errors="replace").read()
        try:
            found.append(json.loads(text))
        except ValueError:
            found.append(text)
print(json.dumps(found))
"""


#: Which calls a `permissions.deny` rule of a kind can refuse, as Claude Code
#: documents them (`project_hooks.denying_rule`): a `Read` rule covers reading
#: a path and editing it, an `Edit` rule editing it. Any other rule names the
#: one tool it is about.
DENIED_BY = {
    "Read": ("Read", "Edit", "Write", "NotebookEdit"),
    "Edit": ("Edit", "Write", "NotebookEdit"),
}


class WorkReady(RuntimeError):
    """The machine was taken just now, and the repository has instructions the
    session has not read: the operation is not run, and these are its answer."""


def _quote(path: str) -> str:
    return shlex.quote(path)


def script_text(name: str) -> str:
    """One of the scripts this package runs on the machine, as source."""
    return importlib.resources.files(__package__).joinpath(f"{name}.py").read_text()


class Machine:
    """The room's machine, as pi's tools reach it.

    ``workspace`` is where the session sees the project: the machine's own path
    once the session was started on it, the platform's placeholder before then
    (``RemoteClient`` spells the placeholder as the machine's path on the way
    out). ``shipped`` is where the runner wrote the platform's own skills on this
    host, ``mirror`` where it keeps its copy of the project's: a path under
    either is spelled where the machine holds that file before it leaves.
    """

    def __init__(
        self,
        target: dict,
        *,
        shipped: Path | None = None,
        mirror: Path | None = None,
        scratch: Path | None = None,
    ):
        self.client = RemoteClient(dict(target))
        self.workspace = str(target.get("workspace") or "")
        self.placeholder = self.workspace == DEFERRED_WORKSPACE
        self.taken = not self.placeholder
        self.shipped = shipped
        self.mirror = mirror
        # pi's own temporary files (a long command's whole output, which its
        # bash names for the model to read) are on this host.
        self.scratch = scratch
        self.executor_config: str | None = None
        # What the repository said when the session first reached the machine
        # (`take`): the session reads it then, and every turn after.
        self.instructions = ""
        # The project's settings as `has_hooks` last read them.
        self.settings: list | None = None
        self.settings_generation: object = None

    # --- where things are ------------------------------------------------------

    def placed(self, text: str) -> str:
        """`text` with every path this host spells for the session written
        where the machine holds that file."""
        if self.mirror is not None:
            # The mirror holds each file at the machine's own absolute path
            # under its root, so dropping the root is the whole translation.
            text = text.replace(str(self.mirror), "")
        if self.shipped is not None and str(self.shipped) in text:
            text = text.replace(str(self.shipped), self._executor_skills())
        return text

    def spelled(self, args: dict) -> dict:
        """A call's arguments as a hook on the machine reads them: every path
        and command written where the machine holds what it names."""
        config = self.client.config
        virtual, real = config.get("virtual_workspace"), config.get("workspace")

        def spell(value: str) -> str:
            value = self.placed(value)
            return value.replace(virtual, real) if virtual and real else value

        return {
            key: spell(value)
            if key in PATH_ARGUMENTS and isinstance(value, str)
            else value
            for key, value in args.items()
        }

    def local(self, path: str) -> Path | None:
        """A file the session reads on this host: the platform's own skills
        (on the machine as well, but read here) and pi's own temporary files."""
        here = Path(path)
        for root in (self.shipped, self.scratch):
            if root is None:
                continue
            try:
                here.relative_to(root)
            except ValueError:
                continue
            return here
        return None

    def _executor_skills(self) -> str:
        if self.executor_config is None:
            self.take()
            status = self.client.call("ping")
            self.executor_config = str(status.get("config_dir") or "")
        return self.executor_config + "/skills"

    # --- the machine itself ---------------------------------------------------

    def take(self, preparing=None) -> None:
        """Hold the room's machine before an operation on it.

        ``RemoteClient`` takes it for every operation that needs it; what this
        adds is the one thing that happens only once: a session started before
        there was a machine has not read the repository's instructions, so the
        operation that brought the machine is not run, and the instructions are
        what it answers with (`WorkReady`). Nothing is refused when the
        repository says nothing."""
        if self.taken:
            return
        self.client.call("project_tools", {}, preparing=preparing)
        self.taken = True
        self.instructions = self.context()
        if self.instructions:
            raise WorkReady(WORK_READY + self.instructions)

    def servers(self) -> list[str]:
        """Every MCP server this session lists (`session_servers`): the
        machine's own stdio servers only once the session runs on it."""
        if self.placeholder:
            return self.client.session_servers()
        return list(self.client.call("project_tools", {})["servers"])

    # --- commands -------------------------------------------------------------

    def start(
        self,
        command_id: str,
        script: str,
        *,
        cwd: str | None = None,
        stdin: bytes | None = None,
        preparing=None,
    ) -> None:
        """Start `script` under `/bin/sh` in `cwd` on the machine, its stdout and
        stderr as one stream, under an id of the caller's."""
        self.take(preparing)
        request = {
            "subtype": "shell",
            "operation": "start",
            "command_id": command_id,
            "kind": "sh",
            "body": self.placed(script),
            "cwd": self.placed(cwd or self.workspace),
            "merge": True,
        }
        if stdin is not None:
            request["stdin"] = base64.b64encode(stdin).decode()
        answer = self.client.control(request, preparing=preparing)
        if not answer.get("started"):
            raise RuntimeError("这条命令在开始之前就被停下了")

    def read(self, command_id: str, offset: int, *, wait: float = READ_WAIT_S) -> dict:
        """What a command printed from `offset` on; its exit status once it has
        ended and everything it printed is read."""
        answer = self.client.control(
            {
                "subtype": "shell",
                "operation": "read",
                "command_id": command_id,
                "out": offset,
                "err": 0,
                "wait": wait,
            }
        )
        data = base64.b64decode(answer.get("out") or "")
        read = {"data": data, "offset": offset + len(data)}
        if "exit" in answer:
            read["exit"] = answer["exit"]
        if answer.get("lost"):
            read["lost"] = True
        return read

    def signal(self, command_id: str, number: int) -> bool:
        """Signal a command and everything it started; whether it was running."""
        answer = self.client.control(
            {
                "subtype": "shell",
                "operation": "signal",
                "command_id": command_id,
                "signal": number,
            }
        )
        return bool(answer.get("running"))

    def run(
        self, script: str, *, cwd: str | None = None, stdin: bytes | None = None
    ) -> tuple[int, bytes]:
        """`script` on the machine, to its end: its exit status and output."""
        command_id = "pi-" + uuid.uuid4().hex
        self.start(command_id, script, cwd=cwd, stdin=stdin)
        output, offset = b"", 0
        while True:
            read = self.read(command_id, offset, wait=20)
            output += read["data"]
            offset = read["offset"]
            if "exit" in read:
                return int(read["exit"]), output
            if read.get("lost"):
                raise RuntimeError("执行机上的这条命令丢了：执行服务在它结束前重启过")

    def _check(self, script: str, *, stdin: bytes | None = None) -> bytes:
        code, output = self.run(script, stdin=stdin)
        if code != 0:
            raise OSError(output.decode("utf-8", "replace").strip() or f"exit {code}")
        return output

    # --- files ----------------------------------------------------------------

    def read_file(self, path: str) -> bytes:
        here = self.local(path)
        if here is not None:
            return here.read_bytes()
        spelled = _quote(path)
        data = self._check(
            f'[ -e {spelled} ] || {{ echo "ENOENT: no such file or directory, '
            f"open '{path}'\"; exit 2; }}; exec head -c {READ_LIMIT + 1} -- {spelled}"
        )
        if len(data) > READ_LIMIT:
            raise OSError(f"{path} 超过 {READ_LIMIT // (1024 * 1024)} MB，读不进来")
        return data

    def access(self, path: str, *, write: bool = False) -> None:
        if not write and self.local(path) is not None:
            return
        spelled = _quote(path)
        writable = f" && [ -w {spelled} ]" if write else ""
        self._check(
            f'[ -e {spelled} ] || {{ echo "ENOENT: no such file or directory, '
            f"access '{path}'\"; exit 2; }}; [ -r {spelled} ]{writable} || "
            f"{{ echo \"EACCES: permission denied, access '{path}'\"; exit 13; }}"
        )

    def image_type(self, path: str) -> str | None:
        here = self.local(path)
        head = (
            here.read_bytes()[:16]
            if here is not None
            else self._check(f"exec head -c 16 -- {_quote(path)}")
        )
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        for magic, kind in IMAGE_MAGIC:
            if head.startswith(magic):
                return kind
        return None

    def write_file(self, path: str, data: bytes) -> None:
        self._check(f"cat > {_quote(path)}", stdin=data)

    def mkdir(self, path: str) -> None:
        self._check(f"mkdir -p -- {_quote(path)}")

    # --- what the project says -------------------------------------------------

    def _on_machine(self, script: str) -> dict:
        """One of this package's scripts, run in the checkout on the machine."""
        return self._on_machine_text(script_text(script))

    def _on_machine_text(self, program: str):
        """A Python program run in the checkout on the machine; what it printed,
        as JSON."""
        output = self._check(
            f"exec python3 - {_quote(self.workspace)}", stdin=program.encode()
        )
        return json.loads(output)

    def context(self) -> str:
        """What the repository says about itself (`repository.py`)."""
        return str(self._on_machine("repository").get("context") or "")

    def project_skills(self) -> list[str]:
        """The project's skills (`project_skills.py`), copied under `mirror`:
        the paths pi is pointed at, which `placed` spells back as the machine's."""
        assert self.mirror is not None
        found = self._on_machine("project_skills")
        for name, content in found["files"].items():
            path = Path(str(self.mirror) + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(content))
        return [str(self.mirror) + skill for skill in found["skills"]]

    # --- the project's hooks ----------------------------------------------------

    def has_hooks(self, event: str, tool: str) -> bool:
        """Whether the project has anything for the machine to run or check
        around a call to `tool` (as the hooks name it) at `event`: a hook whose
        matcher takes it, or (before a call) a `permissions.deny` rule for that
        kind of tool. So a call no hook is written for costs nothing.

        The settings are read once for the machine the session holds, as a
        Claude Code session takes the project's hooks when it starts and again
        when it attaches to a machine (`executor_transport
        .register_project_hooks`), and not on every prompt."""
        generation = self.client.config.get("generation")
        if self.settings is None or generation != self.settings_generation:
            self.settings = self._on_machine_text(SETTINGS)
            self.settings_generation = generation
        for source in self.settings:
            if not isinstance(source, dict):
                return True  # unreadable: the executor says why
            for group in (source.get("hooks") or {}).get(event) or []:
                matcher = group.get("matcher", "*") if isinstance(group, dict) else ""
                try:
                    if matcher in ("", "*") or re.fullmatch(matcher, tool):
                        return True
                except re.error:
                    return True
            if event != "PreToolUse":
                continue
            for rule in (source.get("permissions") or {}).get("deny") or []:
                if isinstance(rule, str) and tool in DENIED_BY.get(
                    rule.split("(", 1)[0], (rule.split("(", 1)[0],)
                ):
                    return True
                if isinstance(rule, str) and rule.startswith("mcp__"):
                    if tool.startswith(rule.split("(", 1)[0].rstrip("*")):
                        return True
        return False

    def hooks(
        self,
        event: str,
        tool: str,
        args: dict,
        *,
        call_id: str,
        cwd: str | None,
        result: object = None,
    ) -> dict:
        """The project's `event` hooks for one call, run on the machine by the
        executor's own rules; the arguments it proceeds with, or ``denied``."""
        request = {
            "subtype": "tool_hooks",
            "event": event,
            "tool": tool,
            "args": args,
            "request_id": call_id,
            "cwd": self.placed(cwd or self.workspace),
        }
        if result is not None:
            request["result"] = result
        return self.client.control(request)
