#!/usr/bin/env python3
"""check-review-artifacts.py — every review artifact is cited by a review note.

WHY THIS EXISTS: `docs/reviews/artifacts/` collects the PDFs attached to the
notes beside it. Nothing reads that directory — no build, no test, no link
checker — so a PDF whose note has been rewritten (or deleted) stays on disk
forever: 328 KB and 348 KB of one-off browser previews sat there named by
nothing until #4032 removed them by hand. The notes are what make an artifact
worth keeping; once no note points at it, the artifact is dead weight that only
a human reading the whole tree would notice.

This guard turns that reading into a check: a file under
`docs/reviews/artifacts/` must be named by at least one document under
`docs/reviews/` (the `artifacts/` subtree itself is not a document).

What counts as a citation:

  * a link destination that resolves to the artifact — inline Markdown links
    (including `<artifacts/2026 review.pdf>`, balanced parentheses, backslash
    escapes and a title after the destination), reference definitions and
    HTML `href` attributes. A fragment or query (`#page=2`, `?raw=1`) is not
    part of the name. The destination must be `artifacts/<name>`, a path
    relative to it (`../artifacts/<name>`, `./artifacts/<name>`), or
    `docs/reviews/artifacts/<name>`. A destination that is just `kept.pdf`
    does not count: it resolves beside the note, not inside `artifacts/`.
    Neither does one with another directory in front (`other/artifacts/…`) or
    an external URL (`https://host/artifacts/…`);
  * the literal path `artifacts/<name>` in prose, under the same prefix rules;
  * a bare `<name>` in prose, when it stands on its own — delimited by spaces
    or punctuation. A bare name glued to Chinese prose (`详见kept.pdf`) is not
    counted, because no rule can tell it apart from a longer Chinese filename
    (`验收报告.pdf` naming `报告.pdf`). Write the `artifacts/` path there.
    A bare name is matched wherever it stands as a whole word, so prose that
    contains another space-separated path (`2026 review.pdf`) also names
    `review.pdf`; link names that contain a space instead.

Percent-encoded paths in prose are decoded and matched too
(`artifacts/2026%20review.pdf`, `2026%20review.pdf`, `%E6%8A%A5%E5%91%8A.pdf`),
as long as the decoded name stands on its own. A literal backslash escape in
prose (`artifacts/report\\(final\\).pdf`) is resolved before matching.

Matching is whole-name with filename boundaries, not substring. That is what
keeps `review.pdf` from being satisfied by `trial-review.pdf`,
`2026.09.review.pdf`, `验收报告.pdf`, `review.pdf.bak`, `review.pdfs`,
`v1+fix.pdf` or `artifacts/run-42/detail.pdf` — all different artifacts. Names
containing spaces, parentheses, `+` or a literal `%` are matched literally.

Cannot judge is not a pass: a missing `docs/reviews/` or `docs/reviews/artifacts/`
directory, a broken symlink where either should be, or a note that cannot be
read exits 2 and says so, because a directory move must not turn this guard
into a green no-op.

Usage: check-review-artifacts.py             check the tree
       check-review-artifacts.py --root DIR  check another tree (tests)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from string import ascii_letters, digits, punctuation
from urllib.parse import unquote

#: Where the artifacts and the notes that must cite them live.
REVIEWS_DIR = Path("docs/reviews")
ARTIFACTS_DIRNAME = "artifacts"

#: Documents a citation may appear in. Notes are Markdown; anything else in
#: `docs/reviews/` (the PDFs themselves) is not a document.
DOC_SUFFIXES = (".md",)

#: Characters that continue an ASCII filename. A match preceded or followed by
#: one of these is a suffix/prefix of a longer name, not a citation. `%` is
#: here so `100%fix.pdf` is not a citation of `fix.pdf`; `+`, `~` and `@` are
#: here because they also appear inside filenames this repository already has.
NAME_CONTINUATION = frozenset(
    ascii_letters + digits + "_-." + "%+~@" + "％＋／＼～＠"
)

#: Characters that mean "this name was cut short", not "the name ends here".
ELLIPSIS = frozenset("…‥")

HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

#: Backslash escapes resolve in prose as well as in link destinations.
ESCAPED_PUNCTUATION = re.compile(r"\\([%s])" % re.escape(punctuation))

#: Reference definitions (`[label]: path`) and HTML links. Their destinations
#: resolve the same way an inline link's does.
REF_DEFINITION = re.compile(r"(?m)^(?: {0,3}> ?)? {0,3}\[[^\]\n]+\]:[ \t]*([^\n]*)")
HTML_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


def _parse_destination(text: str, index: int) -> tuple[str | None, int]:
    """Parse one Markdown link destination; return (path, end) or (None, index)."""
    length = len(text)
    while index < length and text[index] in " \t":
        index += 1
    if index < length and text[index] == "<":
        index += 1
        buffer = []
        while index < length and text[index] not in ">\n":
            if (
                text[index] == "\\"
                and index + 1 < length
                and text[index + 1] in punctuation
            ):
                buffer.append(text[index + 1])
                index += 2
                continue
            buffer.append(text[index])
            index += 1
        if index >= length or text[index] != ">":
            return None, index
        return "".join(buffer), index + 1

    buffer = []
    depth = 0
    while index < length:
        char = text[index]
        if char in " \t\n":
            break
        if (
            char == "\\"
            and index + 1 < length
            and text[index + 1] in punctuation
        ):
            buffer.append(text[index + 1])
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        buffer.append(char)
        index += 1
    if not buffer:
        return None, index
    return "".join(buffer), index


def _parse_inline_link(text: str, index: int) -> tuple[str | None, int, str]:
    """Parse `](destination "title")` with index just after `](`.

    Returns (path, end, status), where status is `ok`, `malformed` (no closing
    parenthesis) or `title_open` (a title quote that never closes). `end` is
    where the destination — and a well-formed title — stopped.
    """
    path, after = _parse_destination(text, index)
    if path is None:
        return None, after, "malformed"
    if "](" in path:
        # A destination cannot contain another link; treat the text as
        # malformed so the inner link is still scanned.
        return path, after, "malformed"
    length = len(text)
    index = after
    # A title may sit on the next line.
    while index < length and text[index] in " \t\n":
        index += 1
    if index < length and text[index] in "\"'":
        quote = text[index]
        index += 1
        while index < length and text[index] != quote:
            index += 2 if text[index] == "\\" else 1
        if index >= length:
            # The destination itself is well formed; only the title runs on.
            return path, after, "title_open"
        index += 1
        while index < length and text[index] in " \t\n":
            index += 1
    elif index < length and text[index] == "(":
        index += 1
        while index < length and text[index] != ")":
            index += 2 if text[index] == "\\" else 1
        if index >= length:
            return path, after, "malformed"
        index += 1
        while index < length and text[index] in " \t\n":
            index += 1
    if index < length and text[index] == ")":
        return path, index + 1, "ok"
    return path, after, "malformed"


def _line_end(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline < 0 else newline


def _reference_end(text: str, after: int) -> int:
    """End of a reference definition's destination and optional title.

    A title may sit on the next line and may contain escaped quotes, so it is
    scanned rather than searched for; the title is part of the definition, not
    prose that can cite an artifact.
    """
    index = after
    while index < len(text) and text[index] in " \t":
        index += 1
    if index < len(text) and text[index] == "\n":
        index += 1
        while index < len(text) and text[index] in " \t":
            index += 1
    if index < len(text) and text[index] in "\"'(":
        closing = ")" if text[index] == "(" else text[index]
        index += 1
        while index < len(text) and text[index] != closing:
            index += 2 if text[index] == "\\" else 1
        if index < len(text):
            return index + 1
    return after


def _has_link_label(text: str, found: int) -> bool:
    """Whether the `](` at `found` closes a link label, not loose brackets."""
    index = found - 1
    while index >= 0:
        char = text[index]
        if char == "[":
            return True
        if char == "(" and index > 0 and text[index - 1] == "]":
            # Reached the `](` of an earlier link first: no label for this one.
            return False
        if char == "\n" and index > 0 and text[index - 1] == "\n":
            return False
        index -= 1
    return False


def _link_spans(text: str):
    """Yield (path, start, end) for every link destination in a document.

    Malformed links yield an empty path with the span that must stay hidden
    from prose, so `[x](kept.pdf` cannot pass as a prose mention.
    """
    index = 0
    while True:
        found = text.find("](", index)
        if found < 0:
            break
        if not _has_link_label(text, found):
            # `](path)` without an opening bracket is not a link.
            index = found + 2
            continue
        path, end, status = _parse_inline_link(text, found + 2)
        if status == "malformed":
            # Hide the malformed destination, then resume right after `](` so
            # a valid link inside it is still found. Prose after the
            # destination on the same line is left alone.
            yield "", found, max(end, found + 2)
            index = found + 2
            continue
        if status == "title_open":
            # Keep the destination and hide the broken title to the end of its
            # line, rescanning inside for a label that is still a link.
            yield path, found, _line_end(text, end)
            index = found + 2
            continue
        yield path, found, end
        index = end

    for match in REF_DEFINITION.finditer(text):
        path, after = _parse_destination(text, match.start(1))
        # Hide the label, the destination and a title, but not the rest of the
        # line: it may be prose naming an artifact (`[^1]: 见 artifacts/kept.pdf`).
        yield path or "", match.start(), _reference_end(text, after)

    for match in HTML_HREF.finditer(text):
        path = match.group(1) or match.group(2) or match.group(3) or ""
        if path:
            yield path, match.start(), match.end()


def _destination_path(destination: str) -> str:
    """The path inside a link destination, without fragment or query."""
    value = destination.strip()
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value.replace("\\", "/")


def _without_link_destinations(text: str, spans=None) -> str:
    out = []
    last = 0
    if spans is None:
        spans = _link_spans(text)
    for _, start, end in sorted(spans, key=lambda span: span[1]):
        if start < last:
            last = max(last, end)
            continue
        out.append(text[last:start])
        out.append(" ")
        last = end
    out.append(text[last:])
    return "".join(out)


def _normalized(value: str, *, decode: bool = True) -> str:
    """A path without a leading slash or `./`, percent-decoding applied."""
    value = unquote(value.strip()) if decode else value.strip()
    value = value.lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _relative_prefix(value: str) -> str:
    """Drop leading `.`/`..` segments from a path."""
    parts = [part for part in value.split("/") if part]
    while parts and parts[0] in (".", ".."):
        parts.pop(0)
    return "/".join(parts)


def path_names_artifact(value: str, relative: str) -> bool:
    """Whether a link destination resolves to `relative` under the artifacts dir."""
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    reviews_relative = f"{REVIEWS_DIR.as_posix()}/{canonical}"
    raw = value.strip()
    if "://" in raw or raw.startswith("//"):
        # An external URL whose path happens to end like a local one.
        return False
    # Percent-decoding is what matches `2026%20review.pdf`; keeping the raw
    # form is what matches a filename that literally contains `%20`.
    for candidate in (_normalized(value), _normalized(value, decode=False)):
        candidate = _relative_prefix(candidate)
        if candidate in (canonical, reviews_relative):
            return True
    return False


def _prefix_before_separator_ok(text: str, separator: int) -> bool:
    """Whether the path in front of a `/` is one of the accepted prefixes."""
    start = separator
    while start > 0 and (
        text[start - 1] in NAME_CONTINUATION or text[start - 1] in "/\\"
    ):
        start -= 1
    prefix = _relative_prefix(text[start:separator].replace("\\", "/"))
    return prefix in ("", REVIEWS_DIR.as_posix())


def _left_boundary_ok(text: str, index: int, *, canonical: bool) -> bool:
    if index == 0:
        return True
    previous = text[index - 1]
    if canonical:
        # `artifacts/` gives the match its shape, so Chinese prose may run
        # straight into it (`详见artifacts/kept.pdf`); only an ASCII filename
        # character means this is the tail of a longer path (`myartifacts/…`).
        if previous in NAME_CONTINUATION:
            return False
        if previous in "/\\":
            return _prefix_before_separator_ok(text, index - 1)
        return True
    # A bare name must stand alone: no filename character of any script and no
    # path separator (`artifacts/run-42/detail.pdf` is not a citation of
    # `detail.pdf`, `详见kept.pdf` is ambiguous with a Chinese filename).
    return (
        previous not in NAME_CONTINUATION
        and previous not in "/\\"
        and not previous.isalnum()
    )


def _right_boundary_text_ok(tail: str, *, canonical: bool) -> bool:
    """Whether the text after a name ends it; `tail` is what comes next."""
    following = tail[0]
    if following in ELLIPSIS:
        # `kept.pdf…` is a name cut short, not the name itself.
        return False
    if following in NAME_CONTINUATION or following in "/\\":
        # A full stop that ends the sentence, rather than one that starts
        # another extension: `kept.pdf.` is a citation, `kept.pdf.bak` is not.
        if following != ".":
            return False
        if len(tail) < 2:
            return True
        after_period = tail[1]
        return (
            after_period not in NAME_CONTINUATION
            and after_period not in "/\\"
            and (canonical or not after_period.isalnum())
        )
    # A bare name glued to a word of another script (`报告.pdf验收`) is
    # ambiguous with a longer name; the `artifacts/` form is not.
    return canonical or not following.isalnum()


def _right_boundary_ok(text: str, end: int, *, canonical: bool) -> bool:
    if end >= len(text):
        return True
    return _right_boundary_text_ok(text[end:], canonical=canonical)


def _decoded_right_boundary_ok(text: str, index: int, *, canonical: bool) -> bool:
    """Right boundary for a percent-encoded name: judge the decoded next char."""
    if index >= len(text):
        return True
    if (
        text[index] == "%"
        and index + 2 < len(text)
        and text[index + 1] in HEX_DIGITS
        and text[index + 2] in HEX_DIGITS
    ):
        tail = unquote(text[index : index + 12])
        if not tail or tail[0] == "�":
            # An incomplete escape (`%E6`) is not a name boundary.
            return False
        return _right_boundary_text_ok(tail, canonical=canonical)
    return _right_boundary_ok(text, index, canonical=canonical)


def _contains(text: str, candidate: str, *, canonical: bool) -> bool:
    start = 0
    while True:
        index = text.find(candidate, start)
        if index < 0:
            return False
        if _left_boundary_ok(
            text, index, canonical=canonical
        ) and _right_boundary_ok(text, index + len(candidate), canonical=canonical):
            return True
        start = index + 1


def _decoded_prefix_matches(
    text: str, start: int, target: str, *, canonical: bool
) -> bool:
    """Whether a percent-encoded prefix starting at `start` decodes to `target`.

    Decoding is incremental: the prefix grows one character at a time, and the
    scan stops at the first character that cannot match the target. Anything
    else is quadratic on a note full of stray `%`.
    """
    # A decoded character costs at most twelve raw characters when written as
    # four three-character `%XX` escapes (a four-byte UTF-8 character).
    limit = min(len(text), start + 12 * len(target) + 12)
    decoded: list[str] = []
    pending = bytearray()
    index = start

    def prefix_text() -> str:
        return "".join(decoded)

    while index < limit:
        char = text[index]
        if (
            char == "%"
            and index + 2 < len(text)
            and text[index + 1] in HEX_DIGITS
            and text[index + 2] in HEX_DIGITS
        ):
            pending.append(int(text[index + 1 : index + 3], 16))
            index += 3
        else:
            if pending:
                decoded.extend(bytes(pending).decode("utf-8", "replace"))
                pending.clear()
            decoded.append(char)
            index += 1
        if pending:
            try:
                decoded.extend(bytes(pending).decode("utf-8"))
            except UnicodeDecodeError:
                # The prefix ends inside a byte sequence; keep reading.
                continue
            pending.clear()
        prefix = prefix_text()
        if len(prefix) > len(target) or not target.startswith(prefix):
            return False
        if len(prefix) == len(target):
            return _decoded_right_boundary_ok(text, index, canonical=canonical)
    return False


def _in_name_run(char: str) -> bool:
    """Whether `char` can sit inside a name whose literal part is Chinese."""
    return char in NAME_CONTINUATION or char.isalnum()


def _percent_encoded_citation(prose: str, relative: str) -> bool:
    """Whether the prose percent-encodes a path naming the artifact."""
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    index = 0
    while True:
        found = prose.find(f"{ARTIFACTS_DIRNAME}/", index)
        if found < 0:
            break
        if _left_boundary_ok(
            prose, found, canonical=True
        ) and _decoded_prefix_matches(prose, found, canonical, canonical=True):
            return True
        index = found + 1

    # A bare name may start before an escape (`2026%20review.pdf`), so check
    # every run of filename characters that contains one — once per run, not
    # once per `%`.
    index = 0
    length = len(prose)
    while index < length:
        if not _in_name_run(prose[index]):
            index += 1
            continue
        start = index
        encoded = False
        while index < length and _in_name_run(prose[index]):
            if prose[index] == "%":
                encoded = True
            index += 1
        if (
            encoded
            and _left_boundary_ok(prose, start, canonical=False)
            and _decoded_prefix_matches(prose, start, relative, canonical=False)
        ):
            return True
    return False


def names_artifact(text: str, relative: str) -> bool:
    """Whether one note names `relative`, a path under the artifacts dir."""
    spans = list(_link_spans(text))
    for path, _, _ in spans:
        if path_names_artifact(_destination_path(path), relative):
            return True
    prose = _without_link_destinations(text, spans)
    unescaped = ESCAPED_PUNCTUATION.sub(r"\1", prose)
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    for candidate, is_canonical in ((canonical, True), (relative, False)):
        for haystack in (prose, unescaped):
            if _contains(haystack, candidate, canonical=is_canonical):
                return True
    return _percent_encoded_citation(prose, relative) or _percent_encoded_citation(
        unescaped, relative
    )


def _artifact_paths(reviews: Path) -> tuple[list[Path], list[Path]]:
    artifacts = reviews / ARTIFACTS_DIRNAME
    if not artifacts.is_dir():
        return [], []
    paths = []
    unreadable = []

    def on_error(exc: OSError) -> None:
        print(f"{exc.filename}: 无法读取: {exc}", file=sys.stderr)
        unreadable.append(Path(exc.filename or artifacts))

    for directory, _, filenames in os.walk(artifacts, onerror=on_error):
        for name in filenames:
            path = Path(directory) / name
            if path.is_file():
                paths.append(path)
    return sorted(paths), unreadable


def _document_texts(reviews: Path) -> tuple[list[str], list[Path]]:
    artifacts = (reviews / ARTIFACTS_DIRNAME).resolve()
    texts = []
    unreadable = []

    def note_problem(path: Path, reason: str) -> None:
        print(f"{path}: 无法读取: {reason}", file=sys.stderr)
        unreadable.append(path)

    def on_error(exc: OSError) -> None:
        note_problem(Path(exc.filename or reviews), str(exc))

    # os.walk reports a directory it cannot enter instead of silently skipping
    # it the way rglob does; a skipped note would turn this guard into a pass.
    for directory, _, filenames in os.walk(reviews, onerror=on_error):
        resolved = Path(directory).resolve()
        if resolved == artifacts or artifacts in resolved.parents:
            # The artifacts subtree is not a document; a broken link in there
            # is not a reason to refuse to judge the notes.
            continue
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.suffix.lower() not in DOC_SUFFIXES:
                continue
            if path.is_symlink() and not path.exists():
                note_problem(path, "断链")
                continue
            if not path.is_file():
                continue
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                note_problem(path, str(exc))
    return texts, unreadable


def unreferenced(artifacts_dir: Path, artifacts: list[Path], documents: list[str]) -> list[Path]:
    """Artifacts no note under `docs/reviews/` names."""
    missing = []
    for artifact in artifacts:
        relative = artifact.relative_to(artifacts_dir).as_posix()
        if not any(names_artifact(text, relative) for text in documents):
            missing.append(artifact)
    return missing


def check(root: Path) -> int:
    reviews = root / REVIEWS_DIR
    if not reviews.is_dir():
        print(f"无法判断：评审文档目录不存在：{reviews}", file=sys.stderr)
        return 2
    artifacts = reviews / ARTIFACTS_DIRNAME
    if artifacts.is_symlink() and not artifacts.exists():
        print(f"无法判断：评审产物目录是断链符号链接：{artifacts}", file=sys.stderr)
        return 2
    if not artifacts.exists():
        print(f"无法判断：评审产物目录不存在：{artifacts}", file=sys.stderr)
        return 2
    if not artifacts.is_dir():
        print(f"无法判断：评审产物路径不是目录：{artifacts}", file=sys.stderr)
        return 2
    if not os.access(artifacts, os.R_OK | os.X_OK):
        print(f"无法判断：评审产物目录读不出来：{artifacts}", file=sys.stderr)
        return 2
    documents, unreadable = _document_texts(reviews)
    if unreadable:
        print(f"无法判断：评审目录里有 {len(unreadable)} 处读不出来", file=sys.stderr)
        return 2
    artifacts_paths, artifact_unreadable = _artifact_paths(reviews)
    if artifact_unreadable:
        print(
            f"无法判断：评审产物目录里有 {len(artifact_unreadable)} 处读不出来",
            file=sys.stderr,
        )
        return 2
    missing = unreferenced(reviews / ARTIFACTS_DIRNAME, artifacts_paths, documents)
    for artifact in missing:
        print(
            f"{artifact}: 没有被 docs/reviews/ 下任何文档引用；"
            f"要么在对应评审笔记里链接它，要么删掉它",
            file=sys.stderr,
        )
    if missing:
        print(f"评审产物守卫发现 {len(missing)} 个零引用产物", file=sys.stderr)
        return 1
    print("评审产物守卫通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="仓库根目录，默认是本脚本所在仓库",
    )
    args = parser.parse_args()
    return check(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
