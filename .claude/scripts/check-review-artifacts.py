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

Names are matched with a left boundary, so `review.pdf` is not satisfied by a
note that only links `trial-review.pdf` — the two are different artifacts, and
a plain substring match would call both referenced.

What is deliberately NOT here: checking links in `docs/manual/**` or whether a
note's prose still describes its artifact. Those are different defects with
different owners; this one is only "the file is named nowhere".

Usage: check-review-artifacts.py             check the tree
       check-review-artifacts.py --root DIR  check another tree (tests)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Where the artifacts and the notes that must cite them live.
REVIEWS_DIR = Path("docs/reviews")
ARTIFACTS_DIRNAME = "artifacts"

#: Documents a citation may appear in. Notes are Markdown; anything else in
#: `docs/reviews/` (the PDFs themselves) is not a document.
DOC_SUFFIXES = (".md",)

#: Characters that may not directly precede a filename for it to count as a
#: mention. Without this, `review.pdf` would match inside `trial-review.pdf`.
NAME_BOUNDARY = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _artifact_paths(reviews: Path) -> list[Path]:
    artifacts = reviews / ARTIFACTS_DIRNAME
    if not artifacts.is_dir():
        return []
    return sorted(path for path in artifacts.rglob("*") if path.is_file())


def _document_texts(reviews: Path) -> list[str]:
    artifacts = (reviews / ARTIFACTS_DIRNAME).resolve()
    texts = []
    for path in sorted(reviews.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        if artifacts in path.resolve().parents:
            continue
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"{path}: 无法读取: {exc}", file=sys.stderr)
    return texts


def _mentions(text: str, candidate: str) -> bool:
    """Whether `text` names `candidate` as a path, not as a suffix of a longer name."""
    start = 0
    while True:
        index = text.find(candidate, start)
        if index < 0:
            return False
        if index == 0 or text[index - 1] not in NAME_BOUNDARY:
            return True
        start = index + 1


def unreferenced(reviews: Path) -> list[Path]:
    """Artifacts no note under `docs/reviews/` names."""
    documents = _document_texts(reviews)
    missing = []
    for artifact in _artifact_paths(reviews):
        referenced_in_reviews = artifact.relative_to(reviews).as_posix()
        bare_name = artifact.relative_to(reviews / ARTIFACTS_DIRNAME).as_posix()
        if not any(
            _mentions(text, referenced_in_reviews) or _mentions(text, bare_name)
            for text in documents
        ):
            missing.append(artifact)
    return missing


def check(root: Path) -> int:
    reviews = root / REVIEWS_DIR
    if not reviews.is_dir():
        print(f"评审文档目录不存在，跳过：{reviews}", file=sys.stderr)
        return 0
    missing = unreferenced(reviews)
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
