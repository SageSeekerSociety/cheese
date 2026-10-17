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

Citations are matched as whole path tokens, not substrings. That is what keeps
`review.pdf` from being satisfied by `trial-review.pdf`, `2026.09.review.pdf`,
`验收报告.pdf`, `review.pdf.bak` or `artifacts/run-42/detail.pdf` — all of them
are different artifacts, and a substring match would call every one of them a
citation. A percent-encoded link (`2026%20review.pdf`) is decoded before the
comparison, so a correctly linked name is not reported as an orphan.

Cannot judge is not a pass: a missing `docs/reviews/` directory or a note that
cannot be read exits 2 and says so, because a directory move must not turn this
guard into a green no-op.

Usage: check-review-artifacts.py             check the tree
       check-review-artifacts.py --root DIR  check another tree (tests)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote

#: Where the artifacts and the notes that must cite them live.
REVIEWS_DIR = Path("docs/reviews")
ARTIFACTS_DIRNAME = "artifacts"

#: Documents a citation may appear in. Notes are Markdown; anything else in
#: `docs/reviews/` (the PDFs themselves) is not a document.
DOC_SUFFIXES = (".md",)

#: Punctuation that continues a path token. Letters and digits of any script
#: join via `str.isalnum()`, so `验收报告.pdf` stays one token and cannot
#: satisfy `报告.pdf`.
PATH_PUNCTUATION = frozenset("_-./%")


def _is_path_char(character: str) -> bool:
    return character.isalnum() or character in PATH_PUNCTUATION


def path_tokens(text: str):
    """The maximal path-shaped runs in `text`."""
    start = None
    for index, character in enumerate(text):
        if _is_path_char(character):
            if start is None:
                start = index
        elif start is not None:
            yield text[start:index]
            start = None
    if start is not None:
        yield text[start:]


def _normalized(token: str) -> str:
    """A token without a leading slash or `./`, percent-decoding applied."""
    value = unquote(token).lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    # A sentence's full stop is not part of the filename; no artifact in this
    # repo ends in `.`, and counting `kept.pdf.` as a different name would make
    # a plain-text citation fail.
    return value.rstrip(".")


def cites(token: str, relative: str) -> bool:
    """Whether one path token names `relative`, a path under the artifacts dir."""
    value = _normalized(token)
    if value == relative:
        return True
    canonical = f"{ARTIFACTS_DIRNAME}/{relative}"
    return value == canonical or value.endswith(f"/{canonical}")


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
        if not any(
            cites(token, relative)
            for text in documents
            for token in path_tokens(text)
        ):
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
