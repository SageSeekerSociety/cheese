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
    part of the name. A destination that is just `kept.pdf` does not count: it
    resolves beside the note, not inside `artifacts/`;
  * the literal path `artifacts/<name>` in prose;
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

Cannot judge is not a pass: a missing `docs/reviews/` directory or a note that
cannot be read exits 2 and says so, because a directory move must not turn this
guard into a green no-op.

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

#: Backslash escapes resolve in prose as well as in link destinations.
ESCAPED_PUNCTUATION = re.compile(r"\\([%s])" % re.escape(punctuation))

#: Reference definitions (`[label]: path`) and HTML links. Their destinations
#: resolve the same way an inline link's does.
REF_DEFINITION = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*([^\n]*)")
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


def _parse_inline_link(text: str, index: int) -> tuple[str | None, int | None]:
    """Parse `](destination "title")` with index just after `](`."""
    path, after = _parse_destination(text, index)
    if path is None:
        return None, None
    length = len(text)
    index = after
    # A title may sit on the next line, and a malformed link must still hide
    # its destination: otherwise `[x](kept.pdf` becomes a prose mention.
    while index < length and text[index] in " \t\n":
        index += 1
    if index < length and text[index] in "\"'":
        quote = text[index]
        index += 1
        while index < length and text[index] != quote:
            index += 2 if text[index] == "\\" else 1
        if index >= length:
            return path, after
        index += 1
        while index < length and text[index] in " \t\n":
            index += 1
    elif index < length and text[index] == "(":
        index += 1
        while index < length and text[index] != ")":
            index += 2 if text[index] == "\\" else 1
        if index >= length:
            return path, after
        index += 1
        while index < length and text[index] in " \t\n":
            index += 1
    if index < length and text[index] == ")":
        return path, index + 1
    return path, after


def _link_spans(text: str):
    """Yield (path, start, end) for every link destination in a document."""
    index = 0
    while True:
        found = text.find("](", index)
        if found < 0:
            break
        path, end = _parse_inline_link(text, found + 2)
        if path is None or end is None:
            index = found + 2
            continue
        yield path, found, end
        index = end

    for match in REF_DEFINITION.finditer(text):
        path, _ = _parse_destination(text, match.start(1))
        if path is not None:
            yield path, match.start(), match.end()

    for match in HTML_HREF.finditer(text):
        path = match.group(1) or match.group(2) or match.group(3) or ""
        if path:
            yield path, match.start(), match.end()


def _destination_path(destination: str) -> str:
    """The path inside a link destination, without fragment or query."""
    value = destination.strip()
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value.replace("\\", "/")


def _without_link_destinations(text: str) -> str:
    out = []
    last = 0
    for _, start, end in sorted(_link_spans(text), key=lambda span: span[1]):
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


def path_names_artifact(value: str, relative: str) -> bool:
    """Whether a link destination resolves to `relative` under the artifacts dir."""
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    # Percent-decoding is what matches `2026%20review.pdf`; keeping the raw
    # form is what matches a filename that literally contains `%20`.
    for candidate in (_normalized(value), _normalized(value, decode=False)):
        if candidate == canonical or candidate.endswith(f"/{canonical}"):
            return True
    return False


def _left_boundary_ok(text: str, index: int, *, canonical: bool) -> bool:
    if index == 0:
        return True
    previous = text[index - 1]
    if canonical:
        # `artifacts/` gives the match its shape, so Chinese prose may run
        # straight into it (`详见artifacts/kept.pdf`); only an ASCII filename
        # character means this is the tail of a longer path (`myartifacts/…`).
        return previous not in NAME_CONTINUATION
    # A bare name must stand alone: no filename character of any script and no
    # path separator (`artifacts/run-42/detail.pdf` is not a citation of
    # `detail.pdf`, `详见kept.pdf` is ambiguous with a Chinese filename).
    return (
        previous not in NAME_CONTINUATION
        and previous not in "/\\"
        and not previous.isalnum()
    )


def _right_boundary_ok(text: str, end: int) -> bool:
    if end >= len(text):
        return True
    following = text[end]
    if following not in NAME_CONTINUATION and following not in "/\\":
        return True
    # A full stop that ends the sentence, rather than one that starts another
    # extension: `kept.pdf.` is a citation, `kept.pdf.bak` is not.
    return following == "." and (
        end + 1 >= len(text)
        or (
            text[end + 1] not in NAME_CONTINUATION
            and text[end + 1] not in "/\\"
        )
    )


def _contains(text: str, candidate: str, *, canonical: bool) -> bool:
    start = 0
    while True:
        index = text.find(candidate, start)
        if index < 0:
            return False
        if _left_boundary_ok(text, index, canonical=canonical) and _right_boundary_ok(
            text, index + len(candidate)
        ):
            return True
        start = index + 1


def _decoded_prefix_matches(text: str, start: int, target: str) -> bool:
    """Whether a percent-encoded prefix starting at `start` decodes to `target`."""
    # A decoded character costs at most a few bytes when written as `%XX`, so
    # a longer prefix cannot decode to this target.
    limit = min(len(text), start + 12 * len(target) + 12)
    end = start
    while end < limit and not text[end].isspace():
        end += 1
    for stop in range(start + 1, end + 1):
        raw = text[start:stop]
        if "%" not in raw:
            continue
        if unquote(raw) == target and _right_boundary_ok(text, stop):
            return True
    return False


def _percent_encoded_citation(prose: str, relative: str) -> bool:
    """Whether the prose percent-encodes a path naming the artifact."""
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    prefix = f"{ARTIFACTS_DIRNAME}/"
    index = 0
    while True:
        found = prose.find(prefix, index)
        if found < 0:
            break
        if _left_boundary_ok(prose, found, canonical=True) and _decoded_prefix_matches(
            prose, found, canonical
        ):
            return True
        index = found + 1

    index = 0
    while True:
        found = prose.find("%", index)
        if found < 0:
            break
        # The name may start before the escape (`2026%20review.pdf`), so walk
        # back to the start of the word and decode from there.
        start = found
        while start > 0 and prose[start - 1] in NAME_CONTINUATION:
            start -= 1
        if _left_boundary_ok(prose, start, canonical=False) and _decoded_prefix_matches(
            prose, start, relative
        ):
            return True
        index = found + 1
    return False


def names_artifact(text: str, relative: str) -> bool:
    """Whether one note names `relative`, a path under the artifacts dir."""
    for path, _, _ in _link_spans(text):
        if path_names_artifact(_destination_path(path), relative):
            return True
    prose = _without_link_destinations(text)
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
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.suffix.lower() not in DOC_SUFFIXES:
                continue
            if path.is_symlink() and not path.exists():
                note_problem(path, "断链")
                continue
            if not path.is_file():
                continue
            if artifacts in path.resolve().parents:
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
    if artifacts.exists() and not os.access(artifacts, os.R_OK | os.X_OK):
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
