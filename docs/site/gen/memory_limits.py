"""The memory limits page's numbers, worked out by the code that enforces them.

`backend/app/domain/memory/files.py` is pure — no IO, no app imports — so this
imports it by path and calls the real `fit_index` / `limit_breach`. The page
then re-computes the same answers in the browser (src/memory-limits.mjs) from
the constants below; build.mjs compares the two and fails if a sample differs,
so a change to the limits cannot leave the page describing the old ones.

Samples are indexed by whole numbers a reader can drag: how many lines the
index has, how many bytes each one takes, how long the line being written is,
how long a memory's body is. The synthetic index is ASCII, so a byte is a
character there and the browser can build the same text.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / "backend" / "app" / "domain" / "memory" / "files.py"
spec = importlib.util.spec_from_file_location("cheese_memory_files", PATH)
files = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = files
spec.loader.exec_module(files)

#: The prefix every synthetic index line starts with (ASCII, so `len` is bytes
#: and characters at once). src/memory-limits.mjs builds lines the same way.
LINE_PREFIX = "- [x](a.md) - "


def index_text(lines: int, line_bytes: int) -> str:
    line = LINE_PREFIX + "x" * max(line_bytes - len(LINE_PREFIX), 0)
    return "".join(f"{line}\n" for _ in range(lines))


def index_case(lines: int, line_bytes: int) -> dict:
    text = index_text(lines, line_bytes)
    kept, warning = files.fit_index(text)
    kept_lines = [line for line in kept.splitlines()]
    return {
        "lines": lines,
        "lineBytes": line_bytes,
        "keptLines": len(kept_lines),
        "keptBytes": len(kept.encode()),
        "truncated": warning is not None,
        "oldLines": text.count("\n"),
        "oldBytes": len(text.encode()),
    }


def line_case(chars: int, already_in_index: bool) -> dict:
    """One index line being written, new or already there.

    A line already in the index does not block this write — the check is about
    what this version adds (`files.limit_breach`).
    """
    line = f"- [x](a.md) - {'x' * max(chars - len(LINE_PREFIX), 0)}"
    previous = f"{line}\n" if already_in_index else ""
    content = previous if already_in_index else f"{line}\n"
    return {
        "chars": chars,
        "alreadyInIndex": already_in_index,
        "rejected": files.limit_breach(files.INDEX_NAME, content, previous) is not None,
    }


def body_case(chars: int) -> dict:
    body = "正" * chars
    content = (
        "---\nname: a-thing\ndescription: 一句话\ntype: project\n---\n\n" f"{body}\n"
    )
    return {"chars": chars, "rejected": files.limit_breach("a-thing.md", content, None) is not None}


json.dump(
    {
        "constants": {
            name: getattr(files, name)
            for name in ("INDEX_MAX_LINES", "INDEX_MAX_BYTES", "INDEX_LINE_MAX", "BODY_MAX", "PATH_MAX", "INDEX_NAME")
        },
        "linePrefix": LINE_PREFIX,
        "indexCases": [
            index_case(lines, line_bytes)
            for lines in (0, 50, 199, 200, 201, 400)
            for line_bytes in (len(LINE_PREFIX), 60, 128, 256, 400)
        ],
        "lineCases": [
            line_case(chars, old)
            for chars in (0, 100, 150, 151, 300, 800)
            for old in (False, True)
        ],
        "bodyCases": [body_case(chars) for chars in (0, 999, 1000, 1001, 2000)],
    },
    sys.stdout,
    ensure_ascii=False,
)
