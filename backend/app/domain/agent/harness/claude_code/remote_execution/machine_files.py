"""The machine's files, for a session whose tools take them one operation at
a time: pi's read, write, edit, ls, find and grep (`pi/platform.ts`).

pi's own tools are built on injected file operations — read these bytes,
does this path exist, list this directory — and on the room's machine each is
one call here, answered by this executor in its own process: no shell started,
no command to read back. Any path the executor's account can reach, as Claude
Code's Read and Write reach any; a relative one is the workspace's.

A search uses ripgrep, as pi's grep and find would: the one the platform
places on every machine (`toolchain.PLACEMENTS`), or the machine's own while
that is still being fetched; with neither it walks what git does not ignore
and matches with `re`.
Either way the answer is in the shape pi's tools format, so the model sees
their output unchanged.

Shipped beside `runtime.py` (`RELEASE_FILES`), standard library only, and run
by Pythons as old as 3.9.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile

#: What pi's find leaves out wherever it searches.
SKIPPED = ("node_modules", ".git")

#: The most one read brings back. pi reads a file whole and then cuts it to
#: what the model is shown; a file past this is refused rather than carried.
READ_LIMIT = 32 * 1024 * 1024

#: What a file's first bytes say it is, for the image types pi hands a model
#: as an image rather than as text (pi's own read does the same sniffing).
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _too_large(path: str) -> str:
    return f"{path} 超过 {READ_LIMIT // (1024 * 1024)} MB，读不进来"


def read(path: str) -> dict:
    with open(path, "rb") as file:
        data = file.read(READ_LIMIT + 1)
    if len(data) > READ_LIMIT:
        return {"error": _too_large(path)}
    return {"data": base64.b64encode(data).decode()}


def _image_type(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    for magic, kind in IMAGE_MAGIC:
        if head.startswith(magic):
            return kind
    return None


def open_file(path: str, write: bool) -> dict:
    """Whether `path` is there to read (and write), and when it is a file,
    what pi reads next: its bytes and whether they are an image. pi's read and
    edit ask whether they may, then read, so one call answers all of it. A
    file past the read limit passes the check and says so where its bytes
    would be: reading it is what fails, as it does on its own."""
    if not os.path.exists(path):
        return {"error": f"ENOENT: no such file or directory, access '{path}'"}
    if not os.access(path, os.R_OK | (os.W_OK if write else 0)):
        return {"error": f"EACCES: permission denied, access '{path}'"}
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as file:
        data = file.read(READ_LIMIT + 1)
    opened: dict = {"type": _image_type(data[:16])}
    if len(data) > READ_LIMIT:
        opened["too_large"] = _too_large(path)
    else:
        opened["data"] = base64.b64encode(data).decode()
    return opened


def write(path: str, data: str) -> dict:
    with open(path, "wb") as file:
        file.write(base64.b64decode(data))
    return {}


def mkdir(path: str) -> dict:
    os.makedirs(path, exist_ok=True)
    return {}


def stat(path: str) -> dict:
    if not os.path.exists(path):
        return {"exists": False}
    return {"exists": True, "directory": os.path.isdir(path)}


def listing(path: str) -> dict:
    with os.scandir(path) as entries:
        return {"entries": [[entry.name, _is_directory(entry)] for entry in entries]}


def _is_directory(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def pattern_regex(pattern: str) -> str:
    """A glob as a regular expression over a `/`-separated path: `**` spans
    directories, `*` and `?` stay within one, `[...]` and `{a,b}` as in fd."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
            continue
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
            continue
        c = pattern[i]
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[" and "]" in pattern[i + 2 :]:
            end = pattern.index("]", i + 2)
            chars = pattern[i + 1 : end]
            if chars.startswith("!"):
                chars = "^" + chars[1:]
            out, i = out + "[" + chars.replace("\\", "\\\\") + "]", end + 1
            continue
        elif c == "{" and "}" in pattern[i:]:
            end = pattern.index("}", i)
            alternatives = pattern[i + 1 : end].split(",")
            out += "(?:" + "|".join(pattern_regex(a) for a in alternatives) + ")"
            i = end + 1
            continue
        else:
            out += re.escape(c)
        i += 1
    return out


def matcher(pattern: str):
    """Whether a file under the search root matches `pattern`, as fd matches a
    glob: against the file's name, or against its whole path once the pattern
    names a directory."""
    if "/" not in pattern:
        named = re.compile(pattern_regex(pattern))
        return lambda root, relative: bool(named.fullmatch(relative.rsplit("/", 1)[-1]))
    if pattern.startswith("/"):
        whole = re.compile(pattern_regex(pattern))
        return lambda root, relative: bool(
            whole.fullmatch(os.path.join(root, relative))
        )
    if not pattern.startswith("**/") and pattern != "**":
        pattern = "**/" + pattern
    under = re.compile(pattern_regex(pattern))
    return lambda root, relative: bool(under.fullmatch(relative))


def ripgrep() -> str | None:
    """The ripgrep a search runs: the platform's, under the toolchain directory
    the executor was started with (`bootstrap`), else one on PATH."""
    toolchain = os.environ.get("CHEESE_TOOLCHAIN")
    if toolchain:
        name = "rg.exe" if os.name == "nt" else "rg"
        placed = os.path.join(toolchain, "bin", name)
        if os.path.isfile(placed) and os.access(placed, os.X_OK):
            return placed
    return shutil.which("rg")


def files(root: str) -> list[str]:
    """Every file under `root` a search looks at, relative to it: hidden ones
    too, and none that git ignores."""
    rg = ripgrep()
    if rg:
        found = subprocess.run(
            [rg, "--files", "--hidden", "--null", "--glob", "!.git"],
            cwd=root,
            capture_output=True,
        ).stdout
    elif _in_git(root):
        found = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
        ).stdout
    else:
        listed = []
        for directory, names, filenames in os.walk(root):
            names[:] = [n for n in names if n not in SKIPPED]
            for name in filenames:
                listed.append(
                    os.path.relpath(os.path.join(directory, name), root).replace(
                        os.sep, "/"
                    )
                )
        return sorted(listed)
    return sorted(
        name.removeprefix("./")
        for name in found.decode("utf-8", "surrogateescape").split("\0")
        if name and os.path.isfile(os.path.join(root, name))
    )


def _in_git(root: str) -> bool:
    if shutil.which("git") is None:
        return False
    return (
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
        ).returncode
        == 0
    )


def glob(pattern: str, path: str, limit: int) -> dict:
    """The files under `path` that `pattern` matches, relative to it."""
    matches = matcher(pattern)
    found = []
    for relative in files(path):
        if any(part in SKIPPED for part in relative.split("/")[:-1]):
            continue
        if matches(path, relative):
            found.append(relative)
            if len(found) >= limit:
                break
    return {"paths": found}


def grep(request: dict) -> dict:
    """Matching lines under a directory or in one file, at most `limit`, each
    with the lines around it that `context` asks for."""
    path = request["path"]
    if not os.path.exists(path):
        return {"error": f"Path not found: {path}"}
    directory = os.path.isdir(path)
    limit = max(1, int(request.get("limit") or 100))
    found = (_rg if ripgrep() else _scan)(request, limit)
    if "error" in found:
        return found
    context = max(0, int(request.get("context") or 0))
    lines_of: dict[str, list[str]] = {}
    matches = []
    for name, number, text in found["matches"]:
        shown = (
            os.path.relpath(name, path).replace(os.sep, "/")
            if directory
            else os.path.basename(name)
        )
        if directory and shown.startswith(".."):
            shown = os.path.basename(name)
        if context == 0 and text is not None:
            matches.append({"path": shown, "line": number, "lines": [[number, text]]})
            continue
        if name not in lines_of:
            lines_of[name] = _lines(name)
        lines = lines_of[name]
        first, last = max(1, number - context), min(len(lines), number + context)
        matches.append(
            {
                "path": shown,
                "line": number,
                "lines": [[n, lines[n - 1]] for n in range(first, last + 1)]
                if lines
                else [],
            }
        )
    return {"matches": matches, "limited": found["limited"]}


def _lines(name: str) -> list[str]:
    try:
        with open(name, encoding="utf-8", errors="replace", newline="") as file:
            text = file.read()
    except OSError:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _rg(request: dict, limit: int) -> dict:
    """The search as pi runs ripgrep for it."""
    args = [
        ripgrep() or "rg",
        "--json",
        "--line-number",
        "--color=never",
        "--hidden",
    ]
    if request.get("ignoreCase"):
        args.append("--ignore-case")
    if request.get("literal"):
        args.append("--fixed-strings")
    if request.get("glob"):
        args += ["--glob", request["glob"]]
    args += ["--", request["pattern"], request["path"]]
    # What ripgrep says goes to a file: it says a line per file it cannot read,
    # and enough of those would fill a pipe nobody reads until it ends.
    said = tempfile.TemporaryFile()
    child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=said)
    assert child.stdout is not None
    matches, limited = [], False
    for line in child.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        name = (data.get("path") or {}).get("text")
        number = data.get("line_number")
        text = (data.get("lines") or {}).get("text")
        if name and isinstance(number, int):
            if text is not None:
                text = text.replace("\r\n", "\n").replace("\r", "").removesuffix("\n")
            matches.append((name, number, text))
        if len(matches) >= limit:
            limited = True
            child.kill()
            break
    code = child.wait()
    said.seek(0)
    error = said.read().decode("utf-8", "replace").strip()
    said.close()
    if not limited and code not in (0, 1):
        return {"error": error or f"ripgrep exited with code {code}"}
    return {"matches": matches, "limited": limited}


def _scan(request: dict, limit: int) -> dict:
    """The same search where the machine has no ripgrep."""
    pattern = request["pattern"]
    if request.get("literal"):
        pattern = re.escape(pattern)
    try:
        wanted = re.compile(pattern, re.IGNORECASE if request.get("ignoreCase") else 0)
    except re.error as error:
        return {"error": f"regex parse error: {error}"}
    path = request["path"]
    if os.path.isdir(path):
        among = matcher(request["glob"]) if request.get("glob") else None
        names = [
            os.path.join(path, relative)
            for relative in files(path)
            if among is None or among(path, relative)
        ]
    else:
        names = [path]
    matches = []
    for name in names:
        try:
            with open(name, "rb") as file:
                data = file.read()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "")
        for number, line in enumerate(text.split("\n"), 1):
            if wanted.search(line):
                matches.append((name, number, line))
                if len(matches) >= limit:
                    return {"matches": matches, "limited": True}
    return {"matches": matches, "limited": False}


def answer(request: dict, root: str = ".") -> dict:
    """One operation; `{"error": ...}` says why it could not be done."""
    operation = request["operation"]
    path = os.path.join(root, request["path"])
    request = {**request, "path": path}
    try:
        if operation == "read":
            return read(path)
        if operation == "open":
            return open_file(path, bool(request.get("write")))
        if operation == "write":
            return write(path, request["data"])
        if operation == "mkdir":
            return mkdir(path)
        if operation == "stat":
            return stat(path)
        if operation == "list":
            return listing(path)
        if operation == "glob":
            return glob(request["pattern"], path, int(request["limit"]))
        if operation == "grep":
            return grep(request)
    except OSError as error:
        return {"error": f"{error.strerror or error}: {path}"}
    raise ValueError(f"Unknown file operation: {operation}")
