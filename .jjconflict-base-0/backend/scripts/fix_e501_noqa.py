"""Append `# noqa: E501` to E501-flagged lines that are SAFE to comment.

`ruff format` already wrapped everything it could; the residue is long string
literals, docstrings, and comments. A line is safe to append a trailing comment
to ONLY if it is not inside a (multi-line) string token — otherwise the `# noqa`
text would silently become part of the string. We use tokenize to find every
line covered by the interior/continuation of a STRING token and skip those; the
rest (code lines, single-line strings, comment lines) get a trailing noqa.

Reports the lines it could NOT safely fix (string/docstring interiors) so those
can be hand-reflowed. Idempotent: never double-adds noqa.

Usage: python scripts/fix_e501_noqa.py <e501_locations_file>
  where the file has `path:line:col:` lines from `ruff check --output-format=concise`.
"""

import sys
import tokenize
from collections import defaultdict
from pathlib import Path


def string_interior_lines(path: Path) -> set[int]:
    """1-indexed line numbers that fall inside a multi-line string token
    (i.e. any line from the token's second line through its last line, plus a
    single-line string is safe so not included)."""
    interior: set[int] = set()
    with path.open("rb") as fh:
        try:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
                    start_row, end_row = tok.start[0], tok.end[0]
                    if end_row > start_row:
                        # lines after the opening line are string content
                        interior.update(range(start_row + 1, end_row + 1))
        except tokenize.TokenError:
            pass
    return interior


def main() -> None:
    locs_file = Path(sys.argv[1])
    by_file: dict[Path, set[int]] = defaultdict(set)
    for raw in locs_file.read_text().splitlines():
        if ": E501" not in raw and ":E501" not in raw:
            continue
        parts = raw.split(":")
        if len(parts) < 2:
            continue
        by_file[Path(parts[0])].add(int(parts[1]))

    fixed = 0
    skipped: list[str] = []
    for path, lines in by_file.items():
        if not path.exists():
            continue
        interior = string_interior_lines(path)
        src = path.read_text().splitlines(keepends=True)
        changed = False
        for ln in sorted(lines):
            idx = ln - 1
            if idx >= len(src):
                continue
            line = src[idx]
            if "noqa" in line:
                continue
            if ln in interior:
                skipped.append(f"{path}:{ln} (string/docstring interior)")
                continue
            newline = "\n" if line.endswith("\n") else ""
            body = line.rstrip("\n")
            src[idx] = f"{body}  # noqa: E501{newline}"
            changed = True
            fixed += 1
        if changed:
            path.write_text("".join(src))

    print(f"noqa added: {fixed}")
    print(f"needs manual reflow (string/docstring interior): {len(skipped)}")
    for s in skipped:
        print(f"  {s}")


if __name__ == "__main__":
    main()
