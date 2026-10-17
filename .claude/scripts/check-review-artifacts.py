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

  * a Markdown link destination that resolves to the artifact — including
    `<artifacts/2026 review.pdf>`, percent-encoded forms, a title after the
    destination (`artifacts/kept.pdf "预览"`) and a fragment or query
    (`artifacts/kept.pdf#page=2`);
  * the literal path `artifacts/<name>` in prose;
  * a bare `<name>` in prose, when it stands on its own — delimited by spaces
    or punctuation. A bare name glued to Chinese prose (`详见kept.pdf`) is not
    counted, because no rule can tell it apart from a longer Chinese filename
    (`验收报告.pdf` naming `报告.pdf`). Write the `artifacts/` path there.

Matching is whole-name with ASCII filename boundaries, not substring. That is
what keeps `review.pdf` from being satisfied by `trial-review.pdf`,
`2026.09.review.pdf`, `验收报告.pdf`, `review.pdf.bak`, `review.pdfs` or
`artifacts/run-42/detail.pdf` — all different artifacts. Names containing
spaces, parentheses, `+` or a literal `%` are matched literally; a path that
percent-encodes them is decoded token by token before the comparison.

Cannot judge is not a pass: a missing `docs/reviews/` directory or a note that
cannot be read exits 2 and says so, because a directory move must not turn this
guard into a green no-op.

Usage: check-review-artifacts.py             check the tree
       check-review-artifacts.py --root DIR  check another tree (tests)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import ascii_letters, digits
from urllib.parse import unquote

#: Where the artifacts and the notes that must cite them live.
REVIEWS_DIR = Path("docs/reviews")
ARTIFACTS_DIRNAME = "artifacts"

#: Documents a citation may appear in. Notes are Markdown; anything else in
#: `docs/reviews/` (the PDFs themselves) is not a document.
DOC_SUFFIXES = (".md",)

#: Characters that continue an ASCII filename. A match preceded or followed by
#: one of these is a suffix/prefix of a longer name, not a citation. `%` is
#: here so `100%fix.pdf` is not a citation of `fix.pdf`.
NAME_CONTINUATION = frozenset(ascii_letters + digits + "_-.%")

#: Markdown link destinations. The angle form may contain spaces and a title
#: after the closing bracket; the plain form may carry a title after the
#: destination. Both stop at the closing parenthesis.
ANGLED_LINK = re.compile(r"\]\(\s*<([^>\n]*)>[^)\n]*\)")
PLAIN_LINK = re.compile(r"\]\(\s*([^)\n]*?)\s*\)")

#: A maximal run that could be a percent-encoded path token. Deliberately
#: excludes whitespace and non-ASCII characters: decoding happens per token, so
#: `2026%20review.pdf` becomes `2026 review.pdf` without inventing a space that
#: turns the tail of a longer name into a separate citation.
ENCODED_TOKEN = re.compile(r"[A-Za-z0-9_\-./%]+")


def _link_destinations(text: str):
    for pattern in (ANGLED_LINK, PLAIN_LINK):
        for match in pattern.finditer(text):
            yield match.group(1)


def _without_link_destinations(text: str) -> str:
    for pattern in (ANGLED_LINK, PLAIN_LINK):
        text = pattern.sub(" ", text)
    return text


def _destination_path(destination: str) -> str:
    """The path inside a link destination, without title, fragment or query."""
    value = destination.strip()
    if value.startswith("<") and ">" in value:
        value = value[1 : value.index(">")]
    else:
        # A plain destination cannot contain a space: a space starts the
        # optional title. An invalid space-containing path therefore cannot
        # name an artifact.
        parts = value.split()
        value = parts[0] if parts else ""
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value.replace("\\", "/")


def _normalized(value: str, *, decode: bool = True) -> str:
    """A path without a leading slash or `./`, percent-decoding applied."""
    value = unquote(value.strip()) if decode else value.strip()
    value = value.lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    # A sentence's full stop is not part of the filename; no artifact in this
    # repo ends in `.`, and counting `kept.pdf.` as a different name would make
    # a plain-text citation fail.
    return value.rstrip(".")


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


def _percent_encoded_citation(prose: str, relative: str) -> bool:
    """Whether a percent-encoded token in the prose names the artifact.

    Only tokens that actually contain an escape are considered: a plain token
    is what the raw search already handled, and decoding it here would let the
    tail of `old\\kept.pdf` match `kept.pdf` after the tokenizer split the path.
    """
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    for match in ENCODED_TOKEN.finditer(prose):
        token = match.group(0)
        if "%" not in token:
            continue
        decoded = unquote(token)
        if decoded == relative:
            canonical_match = False
        elif decoded == canonical or decoded.endswith(f"/{canonical}"):
            canonical_match = True
        else:
            continue
        if not _left_boundary_ok(prose, match.start(), canonical=canonical_match):
            continue
        if not _right_boundary_ok(prose, match.end()):
            continue
        return True
    return False


def names_artifact(text: str, relative: str) -> bool:
    """Whether one note names `relative`, a path under the artifacts dir."""
    for destination in _link_destinations(text):
        if path_names_artifact(_destination_path(destination), relative):
            return True
    prose = _without_link_destinations(text)
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    for candidate, is_canonical in ((canonical, True), (relative, False)):
        if _contains(prose, candidate, canonical=is_canonical):
            return True
    return _percent_encoded_citation(prose, relative)


def _artifact_paths(reviews: Path) -> list[Path]:
    artifacts = reviews / ARTIFACTS_DIRNAME
    if not artifacts.is_dir():
        return []
    return sorted(path for path in artifacts.rglob("*") if path.is_file())


def _document_texts(reviews: Path) -> tuple[list[str], list[Path]]:
    artifacts = (reviews / ARTIFACTS_DIRNAME).resolve()
    texts = []
    unreadable = []
    for path in sorted(reviews.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        if artifacts in path.resolve().parents:
            continue
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"{path}: 无法读取: {exc}", file=sys.stderr)
            unreadable.append(path)
    return texts, unreadable


def unreferenced(reviews: Path, documents: list[str]) -> list[Path]:
    """Artifacts no note under `docs/reviews/` names."""
    missing = []
    for artifact in _artifact_paths(reviews):
        relative = artifact.relative_to(reviews / ARTIFACTS_DIRNAME).as_posix()
        if not any(names_artifact(text, relative) for text in documents):
            missing.append(artifact)
    return missing


def check(root: Path) -> int:
    reviews = root / REVIEWS_DIR
    if not reviews.is_dir():
        print(f"无法判断：评审文档目录不存在：{reviews}", file=sys.stderr)
        return 2
    documents, unreadable = _document_texts(reviews)
    if unreadable:
        print(f"无法判断：{len(unreadable)} 篇评审文档读不出来", file=sys.stderr)
        return 2
    missing = unreferenced(reviews, documents)
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
