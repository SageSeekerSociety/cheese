"""The project's context and the files this executor shows of it.

The context tree a session reads (AGENTS.md and the skills, with the imports
they name), the one directory it may write (`context_fs`), the byte-range reads
of a workflow file, one task's live worktree (`task_fs`), the search over the
checkout (`repo_search`) and the project's own command hooks
(`project_tool_hooks`). `Executor` in `runtime.py` forwards to these bodies.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

if __package__:
    from . import runtime_core as core
else:
    # A script run has this file's directory first on `sys.path`; one loaded by
    # path — `bootstrap` installs one, `predecessor` loads the release's — has
    # not, and the module it reads is the one beside it.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_core as core

# What the bodies below read of the runtime's primitives, named here the way
# they were named when they were `Executor`'s methods.
BINARY = core.BINARY
NOFOLLOW = core.NOFOLLOW
beside = core.beside
gitignored = core.gitignored
native_path = core.native_path
pwrite = core.pwrite
skill_paths = core.skill_paths


def context(self, known_files=None):
    if self.config.get("private"):
        # Never import executable configuration written by a scratch command
        # into the central Claude Code process.
        return {
            "workspace": str(self.root),
            "files": {},
            "instructions": "This chat has temporary scratch space at /work. "
            "Use shell and file tools for drafts and small processing tasks. "
            "Save finished documents through cheese_doc(action=set) and publish artifacts "
            "through cheese show. Scratch files can disappear when execution "
            "is released; they are not permanent storage. "
            "No project checkout is available.",
        }
    files, instructions = {}, []
    seen = set()

    def include(path):
        path = path.resolve()
        if path in seen or not path.is_file():
            return
        seen.add(path)
        text, imported = path.read_text(), []

        def reference(match):
            candidate = Path(match.group(1)).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if candidate.is_file():
                imported.append(candidate)
                return "[included instruction file: " + str(candidate.resolve()) + "]"
            return match.group()

        text = re.sub(r"(?<![\w`])@([^\s`]+)", reference, text)
        instructions.append("# " + str(path) + "\n\n" + text)
        for candidate in imported:
            include(candidate)

    paths = [self.root / "CLAUDE.md", self.root / "CLAUDE.local.md"]
    for path in paths:
        include(path)
    for directory in (self.root / ".claude",):
        if directory.exists():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    file_names = []
    for path in paths:
        if path.is_file():
            name = path.relative_to(self.root).as_posix()
            file_names.append(name)
            content = path.read_bytes()
            if (known_files or {}).get(name) != hashlib.sha256(content).hexdigest():
                files[name] = base64.b64encode(content).decode()
    return {
        "workspace": str(self.root),
        "files": files,
        "file_names": file_names,
        "instructions": "\n\n".join(instructions),
    }


def task_fs(self, params):
    """Read or edit one task's live worktree, never the room's host filesystem."""
    task_id = str(uuid.UUID(params["task_id"]))
    home = Path(self.env["HOME"]).resolve()
    root = (home / ".cheese" / "tasks" / task_id).resolve()
    root.relative_to(home)
    if not root.is_dir():
        return {"error": "not_found", "missing": "worktree"}
    operation = params["operation"]
    if operation == "diff":

        def git(*args, expected=(0,)):
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, timeout=30
            )
            if result.returncode not in expected:
                raise ValueError("Cannot read the task's Git comparison")
            return result.stdout

        base = "refs/remotes/origin/" + params["base_branch"]
        ancestor = git("merge-base", "HEAD", base).decode().strip()
        diff = git("diff", "--no-ext-diff", "--no-textconv", ancestor, "--")
        untracked = git("ls-files", "--others", "--exclude-standard", "-z")
        for name in untracked.decode().split("\0"):
            if not name:
                continue
            path = (root / name).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                diff += git(
                    "diff",
                    "--no-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--",
                    "/dev/null",
                    name,
                    expected=(0, 1),
                )
        return {"diff": diff.decode("utf-8", errors="replace")}
    if operation == "tree":
        listed = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "-c",
                    "-o",
                    "--exclude-standard",
                    "-z",
                ],
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .split("\0")
        )
        files = []
        for name in sorted(set(listed) - {""}):
            path = root / name
            try:
                path.resolve().relative_to(root)
                if path.is_file():
                    files.append({"path": name, "bytes": path.stat().st_size})
            except (ValueError, OSError):
                continue
        return {"files": files}
    name = params.get("path", "")
    relative = Path(name)
    if (
        not name
        or relative.is_absolute()
        or any(p in ("..", ".git") for p in relative.parts)
    ):
        raise ValueError("Invalid task file path")
    path = (root / relative).resolve()
    path.relative_to(root)
    if not path.is_file():
        return {"error": "not_found", "missing": "file"}
    if operation == "read":
        offset = params.get("offset", 0)
        count = params.get("size", 1024 * 1024)
        if (
            not isinstance(offset, int)
            or not isinstance(count, int)
            or offset < 0
            or not 0 <= count <= 2 * 1024 * 1024
        ):
            raise ValueError("Invalid task file byte range")
        with path.open("rb") as file:
            before = os.fstat(file.fileno())
            version = hashlib.file_digest(file, "sha256").hexdigest()[:16]
            if params.get("version") and params["version"] != version:
                return {"error": "conflict"}
            file.seek(offset)
            data = file.read(count)
            after = os.fstat(file.fileno())
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                return {"error": "conflict"}
        return {
            "data": base64.b64encode(data).decode(),
            "bytes": before.st_size,
            "version": version,
        }
    if operation in ("write", "write_bytes"):
        limit = (1 if operation == "write" else 10) * 1024 * 1024
        if path.stat().st_size > limit:
            raise ValueError("Text file is too large")
        old = path.read_bytes()
        version = hashlib.sha256(old).hexdigest()[:16]
        if params.get("version") != version:
            return {"error": "conflict"}
        if operation == "write" and b"\0" in old[:8192]:
            raise ValueError("Binary task files cannot be edited as text")
        if operation == "write":
            old.decode("utf-8")
            data = params["content"].encode("utf-8")
        else:
            data = base64.b64decode(params["data"], validate=True)
        if len(data) > limit:
            raise ValueError("Text file is too large")
        temporary = path.with_name(path.name + ".cheese-save-" + uuid.uuid4().hex)
        try:
            temporary.write_bytes(data)
            temporary.chmod(path.stat().st_mode & 0o777)
            if path.read_bytes() != old:
                return {"error": "conflict"}
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return {"version": hashlib.sha256(data).hexdigest()[:16]}
    raise ValueError("Unknown task filesystem operation")


def repo_search(self, params):
    # A private chat rents no place and has no checkout (结论 19), and its
    # image carries no `repo_search.py`: it says so rather than searching
    # whatever the process happens to be standing in.
    if self.config.get("private"):
        return {"searched": False, "reason": "no-checkout", "hits": []}
    return beside("repo_search")["search"](params, self.root)


def context_fs(self, params):
    """Expose project context, with writes limited to project workflows."""
    if self.config.get("private"):
        if params.get("operation", "tree") != "tree":
            raise ValueError("Private project context is not writable")
        return {"generation": hashlib.sha256(b"private").hexdigest(), "entries": {}}

    root = self.root.resolve()
    operation = params.get("operation", "tree")
    if operation == "statfs" and sys.platform == "win32":
        usage = shutil.disk_usage(root)
        return {
            "f_bsize": 1,
            "f_blocks": usage.total,
            "f_bfree": usage.free,
            "f_bavail": usage.free,
        }
    if operation == "statfs":
        fs = os.statvfs(root)
        return {
            "f_bsize": fs.f_bsize,
            "f_blocks": fs.f_blocks,
            "f_bfree": fs.f_bfree,
            "f_bavail": fs.f_bavail,
        }
    if operation in ("directory", "list"):
        # Project directories are looked up one at a time.
        relative = Path(params.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts:
            return {"missing": True}
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            return {"missing": True}
        if not path.is_dir():
            return {"missing": True}
        if operation == "list":
            names = []
            for child in path.iterdir():
                with contextlib.suppress(OSError, ValueError):
                    child.resolve(strict=True).relative_to(root)
                    if child.is_dir():
                        names.append(child.name)
            return {"directories": sorted(names)}
        stat = path.stat()
        return {
            "mode": stat.st_mode & 0o777,
            "mtime_ns": stat.st_mtime_ns,
            "nlink": stat.st_nlink,
        }
    if operation in {
        "mkdir",
        "create",
        "write",
        "truncate",
        "rename",
        "unlink",
        "rmdir",
    }:

        def workflow_path(name):
            relative = Path(name)
            parts = relative.parts
            claude_directory = operation == "mkdir" and parts == (".claude",)
            if (
                relative.is_absolute()
                or not (
                    claude_directory
                    or len(parts) >= 2
                    and parts[:2] == (".claude", "workflows")
                )
                or (len(parts) == 2 and operation not in {"mkdir", "rmdir"})
                or ".." in parts
                or any(
                    (root.joinpath(*parts[:index])).is_symlink()
                    for index in range(1, len(parts) + 1)
                )
            ):
                raise ValueError("Only project workflows may be changed")
            path = root / relative
            path.resolve().relative_to(root)
            return path

        path = workflow_path(params.get("path", ""))
        if operation == "mkdir":
            path.mkdir(mode=0o700)
        elif operation == "create":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
            os.close(fd)
        elif operation in {"write", "truncate"}:
            fd = os.open(path, os.O_WRONLY | NOFOLLOW | BINARY)
            try:
                if operation == "write":
                    data = base64.b64decode(params["data"], validate=True)
                    offset = params["offset"]
                    if (
                        not isinstance(offset, int)
                        or offset < 0
                        or offset + len(data) > 10 * 1024 * 1024
                    ):
                        raise ValueError("Invalid workflow write range")
                    written = 0
                    while written < len(data):
                        written += pwrite(fd, data[written:], offset + written)
                else:
                    size = params["size"]
                    if not isinstance(size, int) or not 0 <= size <= 10 * 1024 * 1024:
                        raise ValueError("Invalid workflow size")
                    os.ftruncate(fd, size)
                os.fsync(fd)
            finally:
                os.close(fd)
            if operation == "write":
                return {"written": written}
        elif operation == "rename":
            destination = workflow_path(params["destination"])
            os.replace(path, destination)
        elif operation == "unlink":
            path.unlink()
        else:
            path.rmdir()
        return {"ok": True}
    if params.get("operation") == "read":
        name = params.get("path", "")
        entry = self.context_fs_entries.get(name)
        if not entry or entry["kind"] != "file":
            raise FileNotFoundError(name)
        offset = params.get("offset", 0)
        size = params.get("size", 0)
        if (
            not isinstance(offset, int)
            or not isinstance(size, int)
            or offset < 0
            or size < 0
        ):
            raise ValueError("Invalid context filesystem byte range")
        path = root / name
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise FileNotFoundError(name) from exc
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(size)
        return {"data": base64.b64encode(content).decode()}

    selected = set()
    unsupported_imports = set()
    unsupported_paths = set()

    def project_path(path):
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            return None
        return path

    def include(path, *, imports=False, depth=0):
        candidate = project_path(path)
        if candidate is None:
            if path.is_symlink():
                try:
                    unsupported_paths.add(path.relative_to(root).as_posix())
                except ValueError:
                    pass
            return
        path = candidate
        relative = path.relative_to(root)
        if relative in (
            Path(".claude/settings.json"),
            Path(".claude/settings.local.json"),
        ):
            return
        if path in selected or not path.exists():
            return
        selected.add(path)
        if path.is_symlink():
            target = project_path(path.resolve())
            if target is None:
                unsupported_paths.add(path.relative_to(root).as_posix())
            elif target != path:
                include(target, imports=imports, depth=depth)
            return
        if path.is_dir():
            for child in path.iterdir():
                include(child, imports=imports, depth=depth)
            return
        if not imports or path.suffix != ".md" or depth >= 5:
            return
        try:
            text = path.read_text()
        except (OSError, UnicodeError):
            return
        for match in re.finditer(r"(?<![\w`])@([^\s`]+)", text):
            reference = Path(match.group(1)).expanduser()
            if reference.is_absolute():
                unsupported_imports.add(str(reference))
                continue
            candidate = project_path(path.parent / reference)
            if candidate is None:
                unsupported_imports.add(match.group(1))
            elif candidate.exists():
                include(candidate, imports=True, depth=depth + 1)

    for path in (
        root / "AGENTS.override.md",
        root / "AGENTS.md",
        root / "CLAUDE.md",
        root / "CLAUDE.local.md",
        root / ".claude/rules",
        root / ".claude/commands",
        root / ".claude/agents",
    ):
        include(path, imports=True)
    nested_skills = []
    for directory, directories, files in os.walk(root):
        here = Path(directory)
        directories[:] = [n for n in directories if n not in (".git", "node_modules")]
        for name in directories:
            path = here / name
            if path.is_symlink() and project_path(path) is not None:
                selected.add(path)
        for name in (
            "AGENTS.override.md",
            "AGENTS.md",
            "CLAUDE.md",
            "CLAUDE.local.md",
        ):
            if name in files:
                include(here / name, imports=True)
        if here != root and (here / ".claude/skills").is_dir():
            nested_skills.append(here)
    ignored = gitignored(root, nested_skills)
    for here in nested_skills:
        if here not in ignored:
            include(here / ".claude/skills")
    if (root / ".claude").is_dir() and not (root / ".claude").is_symlink():
        selected.add(root / ".claude")
    include(root / ".claude/skills")
    include(root / ".claude/workflows")
    include(root / ".agents/skills")
    include(root / ".codex/skills")

    entries = {}
    for path in selected:
        relative = path.relative_to(root).as_posix()
        stat = path.lstat()
        if path.is_symlink():
            kind = "symlink"
        elif path.is_dir():
            kind = "directory"
        elif path.is_file():
            kind = "file"
        else:
            continue
        entries[relative] = {
            "kind": kind,
            "mode": stat.st_mode & 0o777,
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "nlink": stat.st_nlink,
        }
        if kind == "symlink":
            entries[relative]["target"] = os.readlink(path)
        elif kind == "file" and path.name == "SKILL.md":
            scoped = skill_paths(path)
            if scoped is not None:
                # The files it applies to: the session holds it back
                # until a file tool reaches one (`release.lazy_skills`).
                entries[relative]["paths"] = scoped
        parent = path.parent
        while parent != root:
            name = parent.relative_to(root).as_posix()
            if name not in entries:
                parent_stat = parent.lstat()
                entries[name] = {
                    "kind": "directory",
                    "mode": parent_stat.st_mode & 0o777,
                    "mtime_ns": parent_stat.st_mtime_ns,
                    "size": parent_stat.st_size,
                    "nlink": parent_stat.st_nlink,
                }
            parent = parent.parent

    generation = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if params.get("operation", "tree") == "tree":
        self.context_fs_entries = entries
        return {
            "generation": generation,
            "entries": entries,
            "unsupported_imports": sorted(unsupported_imports),
            "unsupported_paths": sorted(unsupported_paths),
            "hooks": self.project_tool_hooks(),
        }
    raise ValueError("Unsupported context filesystem operation")


def project_tool_hooks(self):
    """The project's own PreToolUse and PostToolUse command hooks.

    The central session registers them, so the build fires them for the
    calls it runs itself — Bash above all — with its own input, and their
    commands run here through its shell prefix. The file tools the plugin
    runs here keep their hooks in `hooks`. A hook that is not a shell
    command string is left out: nothing of the project runs on the
    session host.
    """
    hooks = {}
    for name in ("settings.json", "settings.local.json"):
        path = self.root / ".claude" / name
        try:
            path.resolve(strict=True).relative_to(self.root)
            settings = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for event in ("PreToolUse", "PostToolUse"):
            for group in (settings.get("hooks") or {}).get(event) or []:
                commands = [
                    {
                        key: hook[key]
                        for key in ("type", "command", "timeout")
                        if key in hook
                    }
                    for hook in group.get("hooks") or []
                    if hook.get("type") == "command"
                    and isinstance(hook.get("command"), str)
                    and "args" not in hook
                ]
                if commands:
                    hooks.setdefault(event, []).append(
                        {
                            **(
                                {"matcher": group["matcher"]}
                                if isinstance(group.get("matcher"), str)
                                else {}
                            ),
                            "hooks": commands,
                        }
                    )
    return hooks
