#!/usr/bin/env python3
"""check-i18n-lists.py — the prose that says which i18n namespaces are
directories must agree with the locale trees on disk.

WHY THIS EXISTS. Under `frontend/src/i18n/messages/<locale>/`, a namespace whose
catalog outgrew one file is split into a directory of its own. Four passages name
that set in prose — three in `docs/i18n.md` and one comment in
`docs/site/build.mjs` — and a split that adds or removes a directory has to edit
every one of them. Nothing made anyone: #3825 (splitting `notifications` and
`tasks`, 2026-10-15) left the passages stale and an independent review caught it
as a minor, on the strength of #3780 having updated the same lines by hand the
day before. The set is read as an authoritative, must-be-complete list, so prose
that lags the tree is a defect and the fix has been a remembered chore since.
This check makes it a gate instead.

Two things are checked:

  * the locale trees mirror each other — `zh-CN/` and `en/` hold the same
    top-level directories (`docs/i18n.md` §2: 两个语言目录结构镜像，这是硬要求);
  * each passage lists exactly the directories on disk — no name missing, and
    no name that is not a directory.

A passage is found by the sentence it belongs to, not by a line number, so an
edit above it does not move the check. One that can no longer be found is a
failure, not a pass (exit 2): a check that silently stopped reading a rewritten
sentence guards nothing.

Within a passage only entries that are directories count. The §2 tree also names
file namespaces (`global.json`, `navigation.json`), and the passages that call
`apiError` and `roomNotice` flat tables (`catalog.ts`'s `FLAT` and
`.claude/rules/frontend.md`) are about a different axis: a flat namespace may or
may not be a directory. None of those is read as this set.

Usage: check-i18n-lists.py [--root DIR]   check a tree (default: repo root)
Exit 0 the prose and the trees agree, 1 they do not, 2 the tree could not be judged.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The locale trees, each holding one directory per split namespace.
LOCALES = ("frontend/src/i18n/messages/zh-CN", "frontend/src/i18n/messages/en")

DOC = "docs/i18n.md"
BUILD = "docs/site/build.mjs"

#: Sentences that introduce each passage. Found by text, not by line, so the
#: passages travel with the prose when it moves.
TREE_ROOT = re.compile(r"\s*frontend/src/i18n/messages/\s*")
TREE_LOCALE = re.compile(r"\s*zh-CN/\s*")
BIG_SPLIT = "大词表按子树拆文件"
BIG_SPLIT_END = "各自是一个命名空间"
CJK_HINT = "是目录，见第 2 节"
BUILD_ANCHOR = "directories since the split"

NAME = r"[A-Za-z][A-Za-z0-9_-]*"
BACKTICKED = re.compile(rf"`({NAME})`")
BACKTICKED_DIR = re.compile(rf"`({NAME})/`")


class CannotJudge(Exception):
    """A passage or a tree this check needs is missing or unreadable."""


@dataclass(frozen=True)
class Entry:
    """One directory name a passage names, and the line it is on (1-based)."""

    name: str
    line: int


@dataclass(frozen=True)
class Passage:
    """One prose site, parsed: what it is, where it starts, what it lists."""

    path: str
    what: str
    anchor: int
    entries: tuple[Entry, ...]


def names_in(text: str, line: int) -> list[Entry]:
    return [Entry(m.group(1), line) for m in BACKTICKED.finditer(text)]


def dirs_in(text: str, line: int) -> list[Entry]:
    return [Entry(m.group(1), line) for m in BACKTICKED_DIR.finditer(text)]


def parse_doc_tree(lines: list[str]) -> tuple[list[Entry], int]:
    """The §2 structure block: every `xxx/` row under `zh-CN/` is a directory.

    The file rows (`global.json`, `index.ts`) carry no trailing slash and fall
    out; so does `en/`, a sibling of `zh-CN/` at the same indentation, which ends
    the subtree.
    """
    start = next((i for i, text in enumerate(lines) if TREE_ROOT.fullmatch(text)), None)
    if start is None:
        raise CannotJudge(f"{DOC}: no `frontend/src/i18n/messages/` block")
    zh = next(
        (i for i in range(start + 1, len(lines)) if TREE_LOCALE.fullmatch(lines[i])),
        None,
    )
    if zh is None:
        raise CannotJudge(f"{DOC}: the structure block has no `zh-CN/` line")
    indent = len(lines[zh]) - len(lines[zh].lstrip())
    entries: list[Entry] = []
    for k in range(zh + 1, len(lines)):
        text = lines[k]
        if text.lstrip().startswith("```"):
            break
        if not text.strip():
            continue
        sibling = re.match(r"(\s*)\S+/", text)
        if sibling and len(sibling.group(1)) <= indent:
            break  # left the zh-CN subtree (the `en/` line)
        row = re.match(rf"\s+({NAME})/", text)
        if row:
            entries.append(Entry(row.group(1), k + 1))
    if not entries:
        raise CannotJudge(f"{DOC}: the structure block lists no subdirectory")
    return entries, zh + 1


def parse_doc_big_split(lines: list[str]) -> tuple[list[Entry], int]:
    """The §2 sentence listing the split namespaces with their key counts."""
    start = next((i for i, text in enumerate(lines) if BIG_SPLIT in text), None)
    if start is None:
        raise CannotJudge(f"{DOC}: no sentence saying {BIG_SPLIT}")
    entries: list[Entry] = []
    for k in range(start, len(lines)):
        entries += names_in(lines[k], k + 1)
        if BIG_SPLIT_END in lines[k]:
            break
    else:
        raise CannotJudge(f"{DOC}: the {BIG_SPLIT} sentence never names its set")
    if not entries:
        raise CannotJudge(f"{DOC}: the {BIG_SPLIT} sentence names no namespace")
    return entries, start + 1


def parse_doc_cjk_hint(lines: list[str]) -> tuple[list[Entry], int]:
    """The §5 hint: the parenthetical that calls the same names 是目录."""
    start = next((i for i, text in enumerate(lines) if CJK_HINT in text), None)
    if start is None:
        raise CannotJudge(f"{DOC}: no hint saying {CJK_HINT}")
    head = lines[start][: lines[start].index(CJK_HINT)]
    inner = head[head.rfind("（") + 1 :] if "（" in head else head
    entries = names_in(inner, start + 1)
    if not entries:
        raise CannotJudge(f"{DOC}: the {CJK_HINT} hint names no namespace")
    return entries, start + 1


def parse_build_comment(lines: list[str]) -> tuple[list[Entry], int]:
    """`docs/site/build.mjs`: the comment explaining why every `.json` is read.

    Only the `name/`-shaped backticks in that comment count, so the stray
    `` `.json` `` beside them is not read as a directory.
    """
    anchor = next((i for i, text in enumerate(lines) if BUILD_ANCHOR in text), None)
    if anchor is None:
        raise CannotJudge(f"{BUILD}: no comment saying {BUILD_ANCHOR}")
    start = anchor
    while start - 1 >= 0 and lines[start - 1].lstrip().startswith("//"):
        start -= 1
    entries: list[Entry] = []
    for k in range(start, anchor + 1):
        text = lines[k]
        if k == anchor:
            text = text[: text.index(BUILD_ANCHOR)]
        entries += dirs_in(text, k + 1)
    if not entries:
        raise CannotJudge(f"{BUILD}: the {BUILD_ANCHOR} comment names no directory")
    return entries, start + 1


#: (path, label, parser) for every passage that lists the split namespaces.
PASSAGES = (
    (DOC, "§2 tree", parse_doc_tree),
    (DOC, "§2 big-namespace sentence", parse_doc_big_split),
    (DOC, "§5 CJK-gate hint", parse_doc_cjk_hint),
    (BUILD, "the recursion comment", parse_build_comment),
)


def locale_dirs(root: Path, rel: str) -> set[str]:
    """The top-level directories of one locale tree."""
    where = root / rel
    if not where.is_dir():
        raise CannotJudge(f"{rel} is not a directory")
    return {entry.name for entry in where.iterdir() if entry.is_dir()}


def read_passage(root: Path, path: str, what: str, parse) -> Passage:
    where = root / path
    if not where.is_file():
        raise CannotJudge(f"{path} is not a file")
    entries, anchor = parse(where.read_text(encoding="utf-8").splitlines())
    return Passage(path, what, anchor, tuple(entries))


def judge(root: Path) -> tuple[int, list[str]]:
    """The exit code and the problems: a missing name, an extra one, a mirror break."""
    zh_rel, en_rel = LOCALES
    try:
        zh = locale_dirs(root, zh_rel)
        en = locale_dirs(root, en_rel)
        passages = [
            read_passage(root, path, what, parse) for path, what, parse in PASSAGES
        ]
    except CannotJudge as exc:
        return 2, [f"cannot judge: {exc}"]

    problems: list[str] = []
    for name in sorted(zh - en):
        problems.append(f"{en_rel}: does not mirror `{name}`, a directory in {zh_rel}")
    for name in sorted(en - zh):
        problems.append(f"{zh_rel}: does not mirror `{name}`, a directory in {en_rel}")

    for passage in passages:
        first: dict[str, int] = {}
        for entry in passage.entries:
            first.setdefault(entry.name, entry.line)
        listed = set(first)
        for name in sorted(listed - zh):
            problems.append(
                f"{passage.path}:{first[name]}: lists `{name}` as a directory, "
                f"but {LOCALES[0]}/{name} is not one"
            )
        for name in sorted(zh - listed):
            problems.append(
                f"{passage.path}:{passage.anchor}: {passage.what} does not list "
                f"`{name}`, a directory in {LOCALES[0]}"
            )
    return (1 if problems else 0), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", default=str(REPO_ROOT), help="the tree to check (default: the repo)"
    )
    args = parser.parse_args(argv)

    code, problems = judge(Path(args.root))
    if code == 0:
        print("ok: the i18n directory lists match the locale trees")
    else:
        for problem in problems:
            print(problem, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
