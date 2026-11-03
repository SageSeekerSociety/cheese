"""Opening a pull request is the platform's step, not the agent's.

A task branch's first pushed commit gets its draft PR from the platform
(`review/pr_publish.py`); `cheese_card(action=ready)` marks it ready and
`cheese_card(action=request)` hands it to a person, adopting that same PR. A PR an
agent opens with `gh pr create` has no card behind it, so nothing the platform
watches on a card — checks failing or passing, a review, a conflict — comes
back to the agent, and nobody is asked to accept it.

Each harness refuses such a command where its shell commands pass through the
platform: Claude Code's shell prefix (`remote_execution/shell_prefix.py`), Codex's
executor call (`codex/tools.py`) and pi's PreToolUse (`pi/hooks.py`). Standard
library only: this module ships beside those runners.

The recogniser reads the command as the shell would split it, without running
it, so it is a guard against the ordinary spellings and not a sandbox: a PR
opened through a variable (`$GH pr create`), an alias, a script file, `curl`
against the REST API or a command substitution goes through.
"""

import re
import shlex
import sys

REFUSAL = (
    "PR 由平台开：任务分支推上第一个提交后，平台自己开这条任务的 draft PR"
    "（编号用 `gh pr list --head <分支>` 或任务卡查）。"
    "要评审用 cheese_card(action=ready)，要人验收用 cheese_card(action=request)。"
)

_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", ";;", "|&"}
_SEPARATOR_CHARS = frozenset(";&|()\n")
_SHELLS = {"bash", "sh", "zsh", "dash"}
#: Words in front of a command that leave the command itself unchanged.
_PREFIXES = {"sudo", "env", "command", "exec", "nohup", "time", "builtin"}
#: `gh api` options that take a value, so their value is not the endpoint.
_API_VALUED = {
    "-X",
    "--method",
    "-H",
    "--header",
    "-f",
    "--raw-field",
    "-F",
    "--field",
    "--input",
    "-q",
    "--jq",
    "-t",
    "--template",
    "--hostname",
    "-p",
    "--preview",
    "--cache",
}
_API_BODY = {"-f", "--raw-field", "-F", "--field", "--input"}
_PULLS = re.compile(r"^/?repos/[^/\s]+/[^/\s]+/pulls/?(?:\?.*)?$")
#: What the recogniser falls back to when the shell words do not split.
_LOOSE = re.compile(r"\bgh\s+pr\s+create\b|\bhub\s+pull-request\b")


#: A here-document's operator and its delimiter word, bare or quoted (not
#: `<<<`, a here-string, which is a word on the command line).
_HEREDOC = re.compile(
    r"<<(?!<)(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s;&|<>()'\"\\]+))"
)


def _without_heredoc_bodies(command: str) -> str:
    """``command`` with each here-document's body and each comment left out:
    a body is text handed to a command's input — a script being written, a
    commit message — not a command, and a line of it that reads `gh pr create`
    is not one.

    Only an operator the shell would read starts one: outside quotes and
    comments, or inside a `$( … )` (which reads its own here-documents even
    within double quotes). A command still inside a quoted string (`eval '…'`,
    `bash -c '…'`) keeps its here-documents for the pass its unquoted text
    gets."""
    out: list[str] = []
    pending: list[tuple[str, bool]] = []
    # What encloses the current character: `'`, `"`, or `(` for a `$( … )`
    # or a parenthesis inside one. Empty is the top level.
    within: list[str] = []
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        top = within[-1] if within else ""
        if top == "'":
            if c == "'":
                within.pop()
            out.append(c)
            i += 1
        elif c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
        elif command.startswith("$(", i) and top != "'":
            within.append("(")
            out.append("$(")
            i += 2
        elif top == '"':
            if c == '"':
                within.pop()
            out.append(c)
            i += 1
        elif c in "'\"":
            within.append(c)
            out.append(c)
            i += 1
        elif c == "(" and top == "(":
            within.append("(")
            out.append(c)
            i += 1
        elif c == ")" and top == "(":
            within.pop()
            out.append(c)
            i += 1
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            stop = command.find("\n", i)
            i = n if stop < 0 else stop
        elif c == "\n" and pending:
            out.append(c)
            i += 1
            for delimiter, tabs in pending:
                while i < n:
                    stop = command.find("\n", i)
                    line = command[i : n if stop < 0 else stop]
                    i = n if stop < 0 else stop + 1
                    if (line.lstrip("\t") if tabs else line) == delimiter:
                        break
            pending = []
        elif (
            c == "<"
            and (match := _HEREDOC.match(command, i))
            and (i == 0 or command[i - 1] != "<")
        ):
            word = match.group(2) or match.group(3) or match.group(4)
            pending.append((word, match.group(1) == "-"))
            out.append(match.group(0))
            i = match.end()
        else:
            out.append(c)
            i += 1
    return "".join(out)


def opens_pull_request(command: str) -> bool:
    """Whether running ``command`` would open a pull request."""
    command = _without_heredoc_bodies(command)
    try:
        words = _split(command)
    except ValueError:
        return bool(_LOOSE.search(command))
    return any(_segment_opens(segment) for segment in _segments(words))


def _split(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    # Comments are gone already (`_without_heredoc_bodies`), the way the shell
    # reads them; shlex's own would also cut a word at a `#` inside it.
    lexer.commenters = ""
    return list(lexer)


def _segments(words: list[str]) -> list[list[str]]:
    segments, current = [], []
    for word in words:
        # shlex hands a run of separators over as one token (`;\n`, `\n\n`).
        if word in _SEPARATORS or (word and set(word) <= _SEPARATOR_CHARS):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(word)
    if current:
        segments.append(current)
    return segments


def _segment_opens(words: list[str]) -> bool:
    words = _command_words(words)
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1].lstrip("\\")
    rest = words[1:]
    if name in ("wsl", "wsl.exe"):
        return _segment_opens(_after_wsl(rest))
    if name in _SHELLS:
        script = _shell_script(rest)
        return script is not None and opens_pull_request(script)
    if name == "eval":
        return opens_pull_request(" ".join(rest))
    if name == "gh":
        return _gh_opens(rest)
    if name == "hub":
        return bool(rest) and rest[0] == "pull-request"
    return False


def _command_words(words: list[str]) -> list[str]:
    """The words from the command's name on: assignments, prefixes such as
    `sudo` or `env`, `timeout`'s duration and redirections dropped."""
    index = 0
    while index < len(words):
        word = words[index]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            index += 1
        elif word in _PREFIXES:
            index += 1
        elif word == "timeout":
            index += 1
            while index < len(words) and words[index].startswith("-"):
                index += 1
            index += 1  # the duration
        elif word in ("<", ">", ">>", "<<", "2>", "&>") or re.match(r"^\d*[<>]", word):
            index += 2
        else:
            break
    return [w for w in words[index:] if w not in ("<", ">", ">>", "<<")]


def _after_wsl(words: list[str]) -> list[str]:
    """`wsl [-d DISTRO] [-u USER] [--cd DIR] [-e|--exec|--] COMMAND...`"""
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("-d", "--distribution", "-u", "--user", "--cd"):
            index += 2
        elif word in ("-e", "--exec", "--"):
            return words[index + 1 :]
        elif word.startswith("-"):
            index += 1
        else:
            return words[index:]
    return []


def _shell_script(words: list[str]) -> str | None:
    """The script of `bash -c SCRIPT` (option clusters such as `-lc` too)."""
    for index, word in enumerate(words):
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            return words[index + 1] if index + 1 < len(words) else None
        if not word.startswith("-"):
            return None
    return None


def _gh_opens(words: list[str]) -> bool:
    if words[:2] == ["pr", "create"]:
        return True
    if not words or words[0] != "api":
        return False
    method, body, endpoint, graphql_create = None, False, None, False
    index = 1
    while index < len(words):
        word = words[index]
        flag, inline = word, ""
        if word.startswith("--"):
            flag, _, inline = word.partition("=")
        if flag.startswith("-X") and len(flag) > 2:
            method = flag[2:]
        elif flag in _API_VALUED:
            value = inline or (words[index + 1] if index + 1 < len(words) else "")
            if flag in ("-X", "--method"):
                method = value
            if flag in _API_BODY:
                body = True
                graphql_create = graphql_create or "createPullRequest" in value
            if not inline:
                index += 1
        elif not word.startswith("-") and endpoint is None:
            endpoint = word
        index += 1
    if endpoint is None:
        return False
    if endpoint.strip("/") == "graphql":
        return graphql_create
    posts = (method or ("POST" if body else "GET")).upper() == "POST"
    return posts and bool(_PULLS.match(endpoint))


def refuse_in_shell(command: str) -> int | None:
    """A shell prefix's answer to a command that opens a PR: the refusal on
    stderr and a failing exit status, or None to run the command."""
    if not opens_pull_request(command):
        return None
    sys.stderr.write(REFUSAL + "\n")
    return 1
