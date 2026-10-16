#!/usr/bin/env bash
# Keep message text out of shell arguments. Git hooks still run normally.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: commit-file.sh [FILE|-] [--amend] [--allow-empty] [--quiet]
       commit-file.sh -- FILE

With no FILE (or with -), read standard input. For example:
  .claude/scripts/commit-file.sh <<'MESSAGE'
  fix(example): preserve `foo` and $bar literally
  MESSAGE

Only the listed Git flags are accepted; message overrides, editors, hook
bypasses and pathspecs are not. --amend still requires a new message.
Reject empty/unclosed backticks and empty parentheses outside code spans.
Put literal examples (including foo()) inside balanced backticks, or use a
fenced code block. This is a heuristic: expansion that leaves no visible
trace cannot be detected. Use a file or a quoted heredoc, never an expanded
shell string. Git hooks may enforce their own message rules.
HELP
}

fail() { printf 'commit-file: %s\n' "$*" >&2; exit 2; }
source_path=-
has_source=false
flags=()
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --amend|--allow-empty|--quiet|-q) flags+=("$1") ;;
    --)
      shift
      (($# == 1)) || fail 'expected one file path after --'
      "$has_source" && fail 'only one input file is allowed'
      source_path=$1
      has_source=true
      break
      ;;
    -|[!-]*)
      "$has_source" && fail 'only one input file is allowed'
      source_path=$1
      has_source=true
      ;;
    *) fail "unsupported argument: $1 (see --help)" ;;
  esac
  shift
done

umask 077
message=$(mktemp "${TMPDIR:-/tmp}/commit-file.XXXXXXXXXX")
trap 'rm -f -- "$message"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$source_path" == - ]]; then
  [[ ! -t 0 ]] || fail 'provide a file or a quoted heredoc on standard input'
  cat > "$message"
else
  [[ -f "$source_path" && -r "$source_path" ]] || fail 'input must be a readable regular file'
  cat -- "$source_path" > "$message"
fi

python3 -I - "$message" <<'PY'
import re
import sys
from pathlib import Path


def reject(reason):
    sys.exit(
        "commit-file: " + reason + ". No commit created. "
        "Repair the source file or use a quoted heredoc (<<'MESSAGE'); "
        "put literal syntax examples inside balanced code spans/fences."
    )


raw = Path(sys.argv[1]).read_bytes()
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    reject("message must be UTF-8")
if not text.strip() or b"\0" in raw:
    reject("empty message or NUL byte")

# Match equal-length delimiter runs, so Markdown's double-backtick spans
# can quote a literal backtick. A lone `` remains an unclosed delimiter.
opening = None
start = 0
plain = []
for match in re.finditer(r"`+", text):
    delimiter = match.group()
    if opening is None:
        plain.append(text[start:match.start()])
        opening = delimiter
        start = match.end()
    elif delimiter == opening:
        if not text[start:match.start()].strip():
            reject("empty backtick pair")
        opening = None
        start = match.end()
if opening is not None:
    reject("unclosed or empty backtick pair")
plain.append(text[start:])
if any(re.search(r"\(\s*\)", part) for part in plain):
    reject("empty parentheses outside a code span")
PY

# Do not let commit.cleanup config strip body lines or whitespace. Reject
# flags above that could bypass the validated file or reopen an editor.
git commit "${flags[@]}" --cleanup=verbatim --no-edit -F "$message"
