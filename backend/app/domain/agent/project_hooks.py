"""The project's PreToolUse and PostToolUse hooks, run by whoever makes the call.

A repository writes these in `.claude/settings.json` for plain Claude Code,
which fires them around every tool call. Where Claude Code is not the one
making a call, whatever makes it runs them by these rules: the Codex executor
(`Executor.hooks`), and pi's runner (`harness/pi/hooks.py`). One copy, so what
a hook can block cannot differ between two harnesses:

- the checkout's `.claude/settings.json`, then `.claude/settings.local.json`,
  then whatever the caller adds;
- a group whose `matcher` fully matches the tool name, `""` and `*` matching all;
- command hooks only, run with `bash -c` in the call's working directory, with
  the project root in `CLAUDE_PROJECT_DIR` and the event as JSON on stdin;
- exit 2 blocks with the hook's stderr; any other failing exit lets the call go
  ahead; on success a `deny` decision or `decision: block` blocks, and a
  `PreToolUse` hook's `updatedInput` replaces the call's arguments;
- then the same files' `permissions.deny` rules block a call they match, as
  Claude Code's own permission check would after its hooks (`denying_rule`).

Standard library only, and Python 3.9: shipped as a loose file beside the
executor's runtime (`remote-execution/project_hooks.py`), and in pi's runner
archive.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


class Denied(PermissionError):
    """A hook blocked the call. The message is the hook's reason."""


def run(
    event,
    tool,
    args,
    *,
    call_id,
    root,
    cwd,
    env,
    session_id,
    result=None,
    extra=(),
    program=lambda argv: argv,
):
    """Run the project's `event` hooks for one call to `tool`, as the hooks name
    it; return the arguments the call proceeds with. Raises `Denied` when a hook
    blocks it. `program` turns the hook's argv into one this OS can start."""
    root = Path(root)
    paths = [root / ".claude/settings.json", root / ".claude/settings.local.json"]
    settings = [json.loads(path.read_text()) for path in paths if path.exists()]
    settings.extend(extra)
    for source in settings:
        for group in source.get("hooks", {}).get(event, []):
            matcher = group.get("matcher", "*")
            if matcher not in ("", "*") and not re.fullmatch(matcher, tool):
                continue
            for hook in group.get("hooks", []):
                if hook["type"] != "command":
                    raise ValueError(
                        "Remote execution currently requires command hooks"
                    )
                payload = {
                    "hook_event_name": event,
                    "tool_name": tool,
                    "tool_input": args,
                    "tool_use_id": call_id,
                    "cwd": str(cwd),
                    "session_id": session_id,
                }
                if event == "PostToolUse":
                    payload["tool_response"] = result
                response = subprocess.run(
                    program(["bash", "-c", hook["command"]]),
                    cwd=cwd,
                    env=dict(env, CLAUDE_PROJECT_DIR=str(root)),
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    timeout=hook.get("timeout", 60),
                )
                if response.returncode == 2:
                    raise Denied(f"Remote {event} hook failed: {response.stderr}")
                if response.returncode:
                    continue
                output = json.loads(response.stdout) if response.stdout.strip() else {}
                specific = output.get("hookSpecificOutput", {})
                if (
                    specific.get("permissionDecision") == "deny"
                    or output.get("decision") == "block"
                ):
                    raise Denied(
                        specific.get("permissionDecisionReason")
                        or output.get("reason", "Remote hook denied operation")
                    )
                if event == "PreToolUse" and "updatedInput" in specific:
                    args = specific["updatedInput"]
    if event == "PreToolUse":
        rules = [
            rule
            for source in settings
            for rule in (source.get("permissions") or {}).get("deny") or []
            if isinstance(rule, str)
        ]
        rule = denying_rule(rules, tool, args, root=root, cwd=Path(cwd))
        if rule is not None:
            raise Denied(
                f"The project's settings deny this call ({rule} in permissions.deny)"
            )
    return args


# --- permissions.deny ---------------------------------------------------------
#
# The rules as Claude Code documents them (code.claude.com/docs/en/permissions),
# for the calls the hooks above see, which carry Claude Code's tool names:
#
# - a bare name (`Bash`, `mcp__server`, `mcp__server__*`) denies every call to
#   that tool, or to every tool of that server;
# - `Bash(pattern)` denies a command any of whose subcommands matches: split at
#   `&&`, `||`, `;`, `|`, `|&`, `&` and newlines, looking inside `$(...)` and
#   backticks, past leading variable assignments and the wrappers the docs name.
#   `*` matches anything; a trailing ` *` (or `:*`) that is the only wildcard
#   also matches the bare command, and a pattern without `*` is one command;
# - `Read(path)` denies reading a path and editing it, `Edit(path)` editing it
#   (Edit, Write, NotebookEdit): gitignore-style, `//` from the filesystem root,
#   `~/` from home, `/` from the project, anything else from the working
#   directory and at any depth under it.
#
# A rule is a deny when it matches; there is no allow here that could carve an
# exception out of one, as there is none in Claude Code.

#: Longest first, so `&&` is not read as two `&`.
_OPERATORS = ("&&", "||", "|&", ";", "|", "&", "\n")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*(?:\s+|$)")
_KEYWORDS = ("then", "do", "else", "elif", "if", "while", "until", "!", "{", "(")
#: Wrappers the docs say run their argument as the command. `timeout`, `nice`
#: and `stdbuf` take options (and `timeout` a duration) first (`_strip_one`).
_WRAPPERS = {"time", "nohup", "command", "builtin", "noglob", "xargs"}
_EDITS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def denying_rule(rules, tool, args, *, root, cwd):
    """The first of `rules` that denies this call, or None."""
    for rule in rules:
        name, _, spec = rule.partition("(")
        name = name.strip()
        if not spec:
            if _names(name, tool):
                return rule
            continue
        spec = spec[:-1] if spec.endswith(")") else spec
        if spec in ("", "*") and _names(name, tool):
            return rule
        if name == "Bash" and tool == "Bash":
            if any(_command_matches(spec, c) for c in subcommands(args.get("command"))):
                return rule
        elif (name == "Read" and tool in ("Read",) + _EDITS) or (
            name == "Edit" and tool in _EDITS
        ):
            path = args.get("file_path") or args.get("notebook_path")
            if isinstance(path, str) and _path_matches(spec, path, root=root, cwd=cwd):
                return rule
    return None


def _names(rule, tool):
    if rule == tool:
        return True
    if rule.startswith("mcp__") and "*" not in rule and rule.count("__") == 1:
        return tool.startswith(rule + "__")
    return (
        "*" in rule
        and re.fullmatch(".*".join(re.escape(part) for part in rule.split("*")), tool)
        is not None
    )


def subcommands(command):
    """Every command a shell line runs, as the rules are matched against."""
    if not isinstance(command, str):
        return []
    found = []
    substitution = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
    for inner in substitution.findall(command):
        found += subcommands(inner[0] or inner[1])
    # What a substitution runs is matched on its own, above, not as words of
    # the command it sits in.
    for piece in _split(substitution.sub("_", command)):
        words = piece.strip().rstrip(")}").strip()
        while True:
            stripped = _strip_one(words)
            if stripped == words:
                break
            words = stripped
        if words:
            found.append(" ".join(words.split()))
    return found


def _split(command):
    """The line cut at every operator outside quotes."""
    pieces, start, index, quote = [], 0, 0, None
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                index += 1
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        operator = next(
            (op for op in _OPERATORS if command.startswith(op, index)), None
        )
        if operator is None:
            index += 1
            continue
        pieces.append(command[start:index])
        index += len(operator)
        start = index
    pieces.append(command[start:])
    return pieces


def _strip_one(words):
    """One leading keyword, assignment or wrapper taken off, if there is one."""
    for keyword in _KEYWORDS:
        if words.startswith(keyword + " ") or (
            keyword in "({" and words.startswith(keyword)
        ):
            return words[len(keyword) :].lstrip()
    assignment = _ASSIGNMENT.match(words)
    if assignment:
        return words[assignment.end() :]
    head, _, rest = words.partition(" ")
    if head in _WRAPPERS and rest and not (head == "xargs" and rest.startswith("-")):
        return rest.lstrip()
    if head in ("timeout", "nice", "stdbuf") and rest:
        rest_words = rest.split()
        while rest_words and rest_words[0].startswith("-"):
            flag = rest_words.pop(0)
            if head == "nice" and flag == "-n" and rest_words:
                rest_words.pop(0)
        if head == "timeout" and rest_words:
            rest_words.pop(0)
        return " ".join(rest_words)
    return words


def _command_matches(pattern, command):
    pattern = " ".join(pattern.split())
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    if pattern.count("*") == 1 and pattern.endswith(" *"):
        prefix = pattern[:-2]
        return command == prefix or command.startswith(prefix + " ")
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, command, re.DOTALL) is not None


def _glob(pattern):
    """A gitignore-style path pattern as a regular expression."""
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return "".join(out)


def _path_matches(pattern, path, *, root, cwd):
    target = Path(path).expanduser()
    target = target if target.is_absolute() else Path(cwd) / target
    target = Path(os.path.normpath(str(target)))
    if pattern.startswith("//"):
        base, pattern, anywhere = Path("/"), pattern[2:], False
    elif pattern.startswith("~/"):
        base, pattern, anywhere = Path.home(), pattern[2:], False
    elif pattern.startswith("/"):
        base, pattern, anywhere = Path(root), pattern[1:], False
    else:
        base, pattern, anywhere = (
            Path(cwd),
            pattern[2:] if pattern.startswith("./") else pattern,
            True,
        )
    try:
        relative = target.relative_to(os.path.normpath(str(base))).as_posix()
    except ValueError:
        return False
    expression = ("(?:.*/)?" if anywhere else "") + _glob(pattern.rstrip("/"))
    # A pattern naming a directory names everything under it.
    candidates = [relative] + [
        "/".join(relative.split("/")[:end]) for end in range(1, relative.count("/") + 1)
    ]
    return any(re.fullmatch(expression, c) for c in candidates)
