"""A document's tracked changes, for the panel a person decides in.

#1086 settles the change view for `.docx` as the file's own revisions rather
than a computed diff: the file arrives carrying `<w:ins>` and `<w:del>`, a Word
user already recognises that shape, and it can be accepted or rejected one item
at a time. This module is the platform's half of that — list the items, apply
one decision.

**It loads the shipped skill script rather than reimplementing it.** What counts
as one item is a judgement: a replacement is an insertion and a deletion in the
XML and one decision to a reader. Two implementations of that judgement would
drift, and the first symptom would be the panel numbering an item differently
from the `revisions` command the room just ran — a person accepting item 2 and
getting item 3. There is one answer, in
``backend/sandbox/skills/documents/scripts/office.py``, and both sides read it.

Every row carries its author, and none are filtered out. A document a user
sent may already hold somebody else's unaccepted changes — a colleague's, a
reviewer's — and accepting one of those is an ordinary thing to do in one's own
document. What would not be ordinary is doing it without knowing, so the author
is part of the row rather than a guess made here: `--author` is a display name
the room picks for the reader, not a handle, so there is no honest test for
"this one is ours" and a wrong one is worse than none.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from dataclasses import dataclass
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

#: The script ships to rooms from here; see `agent.skills._NATIVE_SKILL_SRC`.
_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "sandbox"
    / "skills"
    / "documents"
    / "scripts"
    / "office.py"
)

_office: Any | None = None


class RevisionsUnsupported(RuntimeError):
    """Not a format that carries tracked changes."""


class RevisionsFailed(RuntimeError):
    """The document could not be read, or the decision could not be applied."""


def _script() -> Any:
    """The skill script, loaded once.

    Imported by path rather than as a package: `sandbox/` is what ships to other
    people's machines and is deliberately not importable as one.

    Bytecode writing is off for the duration. Python would otherwise leave a
    `__pycache__` next to the script — inside the directory that travels to
    every room, where a `.pyc` is both useless and, on the device path, written
    through a shell heredoc that would corrupt it. `test_native_skill_files.py`
    is what noticed.
    """
    global _office
    if _office is None:
        loader = SourceFileLoader("cheese_office_script", str(_SCRIPT))
        spec = importlib.util.spec_from_loader("cheese_office_script", loader)
        if spec is None:  # pragma: no cover - a missing script is a broken build
            raise RevisionsFailed("找不到处理文档修订的脚本")
        module = importlib.util.module_from_spec(spec)
        quiet = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = quiet
        _office = module
    return _office


@dataclass(frozen=True)
class Revision:
    """One change as a reader sees it, and whose it is."""

    number: int
    part: str
    paragraph: int
    kind: str
    added: str
    removed: str
    author: str
    date: str

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "paragraph": self.paragraph,
            "kind": self.kind,
            "added": self.added,
            "removed": self.removed,
            "author": self.author,
            "date": self.date,
        }


def _package(raw: bytes, path: str, office: Any):
    """`office.Package` over bytes, which it otherwise reads from disk.

    The document may live on a machine that is not this one, so it arrives as
    bytes; `Package` wants a path. Writing a temporary file to hand it back the
    interface it expects is cheaper than a second way to open a package.
    """
    suffix = Path(path).suffix or ".docx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        temporary = Path(handle.name)
    try:
        package = office.Package(temporary)
    except Exception as exc:  # noqa: BLE001 — the script's message is the answer
        temporary.unlink(missing_ok=True)
        raise RevisionsFailed(str(exc)) from exc
    if package.kind() != "word":
        temporary.unlink(missing_ok=True)
        raise RevisionsUnsupported("只有 Word 文档带修订记录")
    return package, temporary


def revisions_in(raw: bytes, path: str) -> list[Revision]:
    """Every tracked change in the document, in the order a reader meets them."""
    office = _script()
    package, temporary = _package(raw, path, office)
    try:
        rows = office._revision_rows(package, "word")
    except Exception as exc:  # noqa: BLE001 — a malformed part reads as unreadable
        raise RevisionsFailed(f"读不出这份文档的修订：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return [
        Revision(
            number=row["number"],
            part=row["part"],
            paragraph=row["paragraph"],
            kind=row["kind"],
            added=row["added"],
            removed=row["removed"],
            author=row["author"],
            date=row["date"],
        )
        for row in rows
    ]


def decide(
    raw: bytes,
    path: str,
    *,
    accept: list[int],
    reject: list[int],
) -> tuple[bytes, list[Revision]]:
    """Apply one or more decisions, and return the document and what is left.

    The numbers address the listing the caller was just shown, so one that is
    not in it means the caller is acting on a different version of the
    document — refused, rather than applied to whatever now sits at that index.
    """
    office = _script()
    known = {r.number: r for r in revisions_in(raw, path)}
    chosen: dict[int, bool] = {}
    for number in accept:
        chosen[number] = True
    for number in reject:
        chosen[number] = False
    if not chosen:
        raise RevisionsFailed("没有说要接受或拒绝哪一处")
    unknown = sorted(n for n in chosen if n not in known)
    if unknown:
        raise RevisionsFailed(
            f"没有第 {'、'.join(str(n) for n in unknown)} 处修订，"
            f"这份文档一共 {len(known)} 处——清单可能已经变了，重新读一次。"
        )

    package, temporary = _package(raw, path, office)
    try:
        paragraph_tag = office._tags("word")[0]
        counted = 0
        for name in package.text_parts():
            root = package.elements(name)
            touched = 0
            for paragraph in root.iter(paragraph_tag):
                for group in office._groups_in(paragraph):
                    counted += 1
                    if counted not in chosen:
                        continue
                    office._apply_one(group, chosen[counted])
                    touched += 1
            if touched:
                package.put(name, root)
        package.save(temporary)
        made = temporary.read_bytes()
    except Exception as exc:  # noqa: BLE001 — the script's message is the answer
        raise RevisionsFailed(str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return made, revisions_in(made, path)
