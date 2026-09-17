#!/usr/bin/env python3
"""Edit an existing Office file without touching the parts you did not edit.

A .docx is a zip of XML parts, and almost all of its formatting lives outside
the part that holds the text: styles, numbering, headers, theme, fonts, and the
relationships between them. Producing a *new* document with python-docx throws
all of that away, and pandoc throws away more. The way to change what a
document says while keeping what it looks like is to edit the text part in
place and copy every other part through unchanged, byte for byte.

These subcommands are the pieces of that:

  unpack    a package into a directory, so any part can be edited by hand
  pack      a directory back into a package, reusing the original part order,
            compression and timestamps
  text      what the document says, paragraph by paragraph, numbered the way
            `edit` reports what it changed
  edit      find and replace text, as Word tracked changes by default
  validate  the package is intact, and rejecting every revision reproduces the
            original text exactly

Two things it deliberately does not do. It never rewrites a part it was not
asked to change, so an edit cannot disturb a page break in a part it never
looked at. And `edit` refuses rather than guesses: a pattern that is absent, or
that occurs more than once, stops the run instead of picking one.

Run it with lxml available:

    uv run --with lxml python3 office.py text 报告.docx
    uv run --with lxml python3 office.py edit 报告.docx -o 改后.docx \\
        --replace "旧的说法=新的说法" --author 芝士
    uv run --with lxml python3 office.py validate 改后.docx --base 报告.docx
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import stat as statmod
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

try:
    import lxml.etree as ET
except ImportError:  # pragma: no cover - the message is the point
    sys.exit(
        "缺少 lxml。请用 uv 运行：uv run --with lxml python3 office.py ...\n"
        "lxml is required and this interpreter does not have it."
    )


# --------------------------------------------------------------------------
# namespaces and element names

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
WQ = f"{{{W}}}"
AQ = f"{{{A}}}"

#: A .docx keeps text in the main part plus the parts holding headers, footers
#: and notes. An edit has to reach all of them: a document whose title lives in
#: a header is the normal case, not the exception.
DOCX_TEXT_PARTS = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$"
)
PPTX_TEXT_PARTS = re.compile(r"^ppt/slides/slide\d+\.xml$")

INS = WQ + "ins"
DEL = WQ + "del"
DEL_TEXT = WQ + "delText"


class Failed(Exception):
    """Something the caller has to fix. Printed to stderr, exit code 2."""


# --------------------------------------------------------------------------
# the package


class Package:
    """A zip of XML parts, held in memory, written back without surprises."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise Failed(f"没有这个文件：{self.path}")
        try:
            z = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile:
            raise Failed(f"{self.path} 不是一个 zip 包，也就不是 Office 文件") from None
        with z:
            bad = z.testzip()
            if bad is not None:
                raise Failed(f"{self.path} 损坏（{bad} 的 CRC 不对），先修好它再改")
            self.names = z.namelist()
            self.infos = {i.filename: i for i in z.infolist()}
            self.blobs = {
                n: (b"" if self.infos[n].is_dir() else z.read(n)) for n in self.names
            }
        if "[Content_Types].xml" not in self.infos:
            raise Failed(f"{self.path} 里没有 [Content_Types].xml，这不是 Office 文件")

    def has(self, name: str) -> bool:
        return name in self.infos

    def elements(self, name: str):
        """Parse a part without letting the parser quietly change the tree."""
        parser = ET.XMLParser(remove_blank_text=False, resolve_entities=False)
        return ET.fromstring(self.blobs[name], parser)

    def put(self, name: str, root) -> None:
        self.blobs[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8")

    def save(self, dest: Path) -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as z:
            for name in self.names:
                info = self.infos[name]
                out = zipfile.ZipInfo(name, date_time=info.date_time)
                out.compress_type = info.compress_type
                out.external_attr = info.external_attr
                out.internal_attr = info.internal_attr
                out.create_system = info.create_system
                z.writestr(out, self.blobs[name])

    def kind(self) -> str:
        suffix = self.path.suffix.lower()
        if suffix in (".pptx", ".pptm", ".potx"):
            return "slide"
        if suffix in (".xlsx", ".xlsm", ".xltx"):
            return "sheet"
        return "word"

    def text_parts(self) -> list[str]:
        kind = self.kind()
        pattern = {"slide": PPTX_TEXT_PARTS, "sheet": None}.get(kind, DOCX_TEXT_PARTS)
        if pattern is None:
            return []
        return [n for n in self.names if pattern.match(n)]


# --------------------------------------------------------------------------
# text: where the words are


def _tags(kind: str):
    """(paragraph, run, text, run properties) for this document kind."""
    if kind == "slide":
        return AQ + "p", AQ + "r", AQ + "t", AQ + "rPr"
    return WQ + "p", WQ + "r", WQ + "t", WQ + "rPr"


class Segment:
    """One live text node: the run holding it and its offset in the paragraph."""

    __slots__ = ("run", "node", "text", "start")

    def __init__(self, run, node, text, start):
        self.run, self.node, self.text, self.start = run, node, text, start

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _ancestor_is(element, tag: str) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.getparent()
    return False


def segments_of(paragraph, kind: str) -> list[Segment]:
    """Live text of one paragraph, in document order.

    Text inside <w:del> is left out: the reader cannot see it, it is a record
    of what was taken out, and a search that matched it would "edit" a sentence
    nobody can read.
    """
    _, run_tag, text_tag, _ = _tags(kind)
    segments: list[Segment] = []
    offset = 0
    for run in paragraph.iter(run_tag):
        if kind == "word" and _ancestor_is(run, DEL):
            continue
        for node in run.iter(text_tag):
            text = node.text or ""
            segments.append(Segment(run, node, text, offset))
            offset += len(text)
    return segments


def paragraph_text(paragraph, kind: str) -> str:
    return "".join(s.text for s in segments_of(paragraph, kind))


def annotated_text(paragraph, kind: str) -> str:
    """Paragraph text with any revisions spelled out, for a human to read."""
    _, run_tag, text_tag, _ = _tags(kind)
    out: list[str] = []
    for run in paragraph.iter(run_tag):
        # Removed text lives in <w:delText>, not <w:t>: the point of showing
        # revisions is to see what is no longer there.
        nodes = list(run.iter(text_tag)) + list(run.iter(DEL_TEXT))
        text = "".join(n.text or "" for n in nodes)
        has_break = any(True for _ in run.iter(WQ + "br"))
        if kind != "word":
            out.append(text)
        elif _ancestor_is(run, DEL):
            if text:
                out.append(f"⟦-{text}⟧")
        elif _ancestor_is(run, INS):
            if text:
                out.append(f"⟦+{text}⟧")
        else:
            out.append(text)
        if has_break:
            out.append("\n")
    return "".join(out)


# --------------------------------------------------------------------------
# editing


def set_text(node, text: str) -> None:
    node.text = text
    # A leading or trailing space is dropped by some readers unless the element
    # says otherwise, and the difference only shows up when someone copies the
    # text out of the finished file.
    if text != text.strip():
        node.set(f"{{{XML}}}space", "preserve")


def split_runs(paragraph, kind: str) -> None:
    """Give every text node a run of its own.

    One run can hold several text nodes around a line break or a tab. Changing
    part of such a run means cutting it into pieces, and a piece cannot take
    the break with it without taking it out of the document. Splitting first
    makes every later step local: after this, a run holding text holds nothing
    else.
    """
    _, run_tag, text_tag, rpr_tag = _tags(kind)
    for run in list(paragraph.iter(run_tag)):
        children = list(run)
        if not children:
            continue
        rpr = children[0] if children[0].tag == rpr_tag else None
        body = children[1:] if rpr is not None else children
        texts = [c for c in body if c.tag == text_tag]
        if len(texts) <= 1 and (body == texts or not texts):
            continue  # already one text node per run

        groups: list[list] = []
        for child in body:
            if child.tag == text_tag:
                groups.append([child])
            elif not groups or groups[-1][0].tag == text_tag:
                groups.append([child])
            else:
                groups[-1].append(child)

        parent = run.getparent()
        index = list(parent).index(run)
        for group in groups:
            new_run = ET.Element(run_tag)
            if rpr is not None:
                new_run.append(copy.deepcopy(rpr))
            for child in group:
                new_run.append(child)
            parent.insert(index, new_run)
            index += 1
        parent.remove(run)


def clone_run_with_text(run, text_tag: str, rpr_tag: str, text: str):
    """A copy of a run carrying just its formatting and the given text."""
    clone = ET.Element(run.tag)
    if len(run) and run[0].tag == rpr_tag:
        clone.append(copy.deepcopy(run[0]))
    node = ET.SubElement(clone, text_tag)
    set_text(node, text)
    return clone


def _outside_revisions(element):
    """Step out of a revision wrapper: a new revision is a sibling of the old
    one, never a child. Nesting <w:ins> inside <w:ins> renders, but it reads as
    two revisions of the same text, and nobody can accept one without the
    other."""
    node, parent = element, element.getparent()
    while parent is not None and parent.tag in (INS, DEL):
        node, parent = parent, parent.getparent()
    return node, parent


def _next_revision_id(root) -> int:
    ids = []
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag in (INS, DEL):
            try:
                ids.append(int(el.get(WQ + "id") or 0))
            except ValueError:
                pass
    return max(ids, default=0) + 1


def _make_revision(tag: str, author: str, date: str, counter: list):
    el = ET.Element(tag)
    el.set(WQ + "id", str(counter[0]))
    el.set(WQ + "author", author)
    el.set(WQ + "date", date)
    counter[0] += 1
    return el


def replace_once(
    paragraph,
    kind: str,
    pattern: str,
    replacement: str,
    offset: int,
    track: bool,
    author: str,
    date: str,
    counter: list,
) -> int:
    """Replace the first occurrence of `pattern` at or after `offset`.

    Returns the offset just past the new text, or -1 when there is no further
    occurrence. Returning a position rather than restarting from the top is
    what keeps `--all` from looping forever when the replacement contains the
    pattern (renaming 文件 to 文件系统 is the ordinary case).
    """
    _, run_tag, text_tag, rpr_tag = _tags(kind)
    split_runs(paragraph, kind)
    segments = segments_of(paragraph, kind)
    flat = "".join(s.text for s in segments)
    at = flat.find(pattern, offset)
    if at < 0:
        return -1
    end = at + len(pattern)
    affected = [s for s in segments if s.end > at and s.start < end]
    if not affected:
        return -1

    # Cut each affected run down to the part the match covers, leaving the text
    # around it - and so its formatting, and its own revision history - in runs
    # of its own.
    mids = []
    for seg in affected:
        local_start = max(at, seg.start) - seg.start
        local_end = min(end, seg.end) - seg.start
        run, parent = seg.run, seg.run.getparent()
        index = list(parent).index(run)
        pieces = []
        if local_start > 0:
            pieces.append(
                clone_run_with_text(run, text_tag, rpr_tag, seg.text[:local_start])
            )
        mid = clone_run_with_text(
            run, text_tag, rpr_tag, seg.text[local_start:local_end]
        )
        pieces.append(mid)
        if local_end < len(seg.text):
            pieces.append(
                clone_run_with_text(run, text_tag, rpr_tag, seg.text[local_end:])
            )
        for piece in pieces:
            parent.insert(index, piece)
            index += 1
        parent.remove(run)
        mids.append(mid)

    first = mids[0]
    anchor, anchor_parent = _outside_revisions(first)

    # A sentence can be spread over several runs. Group the covered runs that
    # ended up next to each other into one revision, or every run boundary
    # inside the sentence would read as a separate edit.
    groups: list[list] = []
    for mid in mids:
        if groups and mid.getparent() is groups[-1][-1].getparent():
            siblings = list(mid.getparent())
            if siblings.index(mid) == siblings.index(groups[-1][-1]) + 1:
                groups[-1].append(mid)
                continue
        groups.append([mid])

    if not track:
        # No revision markup: the replacement takes the place of the text it
        # replaces, so the document reads as if it had always said this. The
        # covered runs in the other segments are dropped rather than emptied -
        # an empty run at a run boundary is invisible in Word but shows up in
        # every diff of the XML.
        for index, mid in enumerate(mids):
            if index == 0 and replacement:
                set_text(mid.find(text_tag), replacement)
            else:
                mid.getparent().remove(mid)
        return at + len(replacement)

    if replacement:
        lines = replacement.split("\n")
        new_run = clone_run_with_text(affected[0].run, text_tag, rpr_tag, lines[0])
        for line in lines[1:]:
            new_run.append(ET.Element(WQ + "br"))
            set_text(ET.SubElement(new_run, text_tag), line)
        holder = _make_revision(INS, author, date, counter)
        holder.append(new_run)
        anchor_parent.insert(list(anchor_parent).index(anchor), holder)

    for group in groups:
        if any(_ancestor_is(mid, INS) for mid in group):
            # This text was itself an unaccepted insertion, and is being
            # changed again. A deletion revision around it would claim the text
            # had been in the document and was then removed; the truth is that
            # it was never accepted and is simply gone. Take it out.
            wrapper = group[0].getparent()
            for mid in group:
                mid.getparent().remove(mid)
            while wrapper is not None and wrapper.tag == INS and len(wrapper) == 0:
                up = wrapper.getparent()
                up.remove(wrapper)
                wrapper = up
            continue
        wrapper = _make_revision(DEL, author, date, counter)
        parent = group[0].getparent()
        parent.insert(list(parent).index(group[0]), wrapper)
        for mid in group:
            parent = mid.getparent()
            parent.remove(mid)
            for node in mid.iter(text_tag):
                node.tag = DEL_TEXT
            wrapper.append(mid)

    return at + len(replacement)


def insert_after(
    paragraph,
    kind: str,
    anchor_text: str,
    new_text: str,
    offset: int,
    track: bool,
    author: str,
    date: str,
    counter: list,
) -> int:
    """Insert `new_text` after the first occurrence of `anchor_text`."""
    _, run_tag, text_tag, rpr_tag = _tags(kind)
    split_runs(paragraph, kind)
    segments = segments_of(paragraph, kind)
    flat = "".join(s.text for s in segments)
    at = flat.find(anchor_text, offset)
    if at < 0:
        return -1
    end = at + len(anchor_text)
    covered = [s for s in segments if s.end <= end and s.end > at]
    if not covered:
        return -1
    node, parent = _outside_revisions(covered[-1].run)
    index = list(parent).index(node) + 1

    lines = new_text.split("\n")
    run = clone_run_with_text(covered[-1].run, text_tag, rpr_tag, lines[0])
    for line in lines[1:]:
        run.append(ET.Element(WQ + "br"))
        set_text(ET.SubElement(run, text_tag), line)
    if track:
        holder = _make_revision(INS, author, date, counter)
        holder.append(run)
        parent.insert(index, holder)
    else:
        parent.insert(index, run)
    return end


# --------------------------------------------------------------------------
# subcommands


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_unpack(args) -> int:
    source = Path(args.package)
    package = Package(source)
    dest = Path(args.directory).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in package.names:
        info = package.infos[name]
        if statmod.S_ISLNK(info.external_attr >> 16):
            # Nothing in an Office package is a link. One that claims to be is
            # someone else's file trying to leave this directory.
            raise Failed(
                f"{name} 在包里是个符号链接。Office 文件不含链接，"
                "这个包来路可疑，不拆它。"
            )
        target = (dest / name).resolve()
        if target != dest and dest not in target.parents:
            raise Failed(f"{name} 指向了目录之外，不拆这个包")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.blobs[name])
        # Directory entries are recorded too: Word packages carry them, pack
        # has to write them back, and a package without them still opens --
        # which is exactly the kind of difference nobody notices until it is
        # compared.
        entries.append(
            {
                "name": name,
                "date_time": list(info.date_time),
                "compress_type": info.compress_type,
                "external_attr": info.external_attr,
                "internal_attr": info.internal_attr,
                "create_system": info.create_system,
            }
        )
    (dest / ".office-package.json").write_text(
        json.dumps(
            {"source": str(source.resolve()), "entries": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"拆开 {source} → {dest}（{len(entries)} 个部件）")
    print("改完用 pack 装回去；目录里的 .office-package.json 要留着。")
    return 0


def cmd_pack(args) -> int:
    directory = Path(args.directory)
    manifest_path = directory / ".office-package.json"
    if not manifest_path.is_file():
        raise Failed(f"{directory} 里没有 .office-package.json，先用 unpack 拆开")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    missing = []
    with zipfile.ZipFile(dest, "w") as z:
        for entry in manifest["entries"]:
            path = directory / entry["name"]
            if entry["name"].endswith("/"):
                blob = b""
            elif path.is_file():
                blob = path.read_bytes()
            else:
                missing.append(entry["name"])
                continue
            info = zipfile.ZipInfo(entry["name"], date_time=tuple(entry["date_time"]))
            info.compress_type = entry["compress_type"]
            info.external_attr = entry["external_attr"]
            info.internal_attr = entry["internal_attr"]
            info.create_system = entry["create_system"]
            z.writestr(info, blob)
    if missing:
        dest.unlink(missing_ok=True)
        shown = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        raise Failed(
            f"这些部件原包里有、现在磁盘上没有：{shown}\n"
            "少了任何一个部件，Word 都会说文件已损坏。"
        )
    print(f"装回 {dest}（{len(manifest['entries'])} 个部件）")
    return 0


def cmd_text(args) -> int:
    package = Package(Path(args.package))
    kind = package.kind()
    parts = package.text_parts()
    if not parts:
        raise Failed("Excel 的文字不在这些部件里。读 .xlsx 用 openpyxl，见 SKILL.md。")
    if args.part:
        parts = [p for p in parts if p == args.part or p.endswith(args.part)]
        if not parts:
            raise Failed(f"包里没有 {args.part}")
    p_tag = _tags(kind)[0]
    for name in parts:
        root = package.elements(name)
        paragraphs = list(root.iter(p_tag))
        if len(parts) > 1:
            print(f"--- {name}")
        print(f"（{len(paragraphs)} 段）")
        for index, paragraph in enumerate(paragraphs):
            body = (
                annotated_text(paragraph, kind)
                if args.revisions
                else paragraph_text(paragraph, kind)
            )
            print(f"[{index:>4}] {body}")
    return 0


def _pair(raw: str, flag: str) -> tuple[str, str]:
    if "=" not in raw:
        raise Failed(f"{flag} 要写成「原文=新文本」，收到的是 {raw!r}")
    left, right = raw.split("=", 1)
    if not left:
        raise Failed(f"{flag} 的原文是空的，说明不了改哪里")
    return left, right


def _edit_targets(package, kind: str, pattern: str) -> list[tuple[str, int, int]]:
    """(part, paragraph index, occurrences) for every paragraph holding `pattern`."""
    p_tag = _tags(kind)[0]
    found = []
    for name in package.text_parts():
        root = package.elements(name)
        for index, paragraph in enumerate(root.iter(p_tag)):
            count = paragraph_text(paragraph, kind).count(pattern)
            if count:
                found.append((name, index, count))
    return found


def cmd_edit(args) -> int:
    source = Path(args.package)
    package = Package(source)
    kind = package.kind()
    if kind == "sheet":
        raise Failed("Excel 的文字不在这些部件里，改 .xlsx 用 openpyxl，见 SKILL.md。")
    dest = Path(args.output) if args.output else source

    jobs: list[tuple[str, str, str]] = []
    for raw in args.replace or []:
        old, new = _pair(raw, "--replace")
        jobs.append(("replace", old, new))
    for old in args.delete or []:
        if not old:
            raise Failed("--delete 后面要给要删掉的原文")
        jobs.append(("replace", old, ""))
    for raw in args.insert_after or []:
        anchor, new = _pair(raw, "--insert-after")
        jobs.append(("insert", anchor, new))
    if not jobs:
        raise Failed("没有说要改什么：用 --replace / --delete / --insert-after")
    if args.track and kind != "word":
        raise Failed(
            "修订标记只有 Word 有，PowerPoint 里没有这个东西；去掉 --track 才是直接改。"
        )
    if args.occurrence and args.all:
        raise Failed("--occurrence 和 --all 只能给一个")

    date = _now()
    touched: dict[str, int] = {}

    for flag, old, new in jobs:
        hits = _edit_targets(package, kind, old)
        total = sum(h[2] for h in hits)
        if total == 0:
            raise Failed(
                f"没找到 {old!r}。\n"
                "文字可能被拆在几段或几个部件里，或者跟预览里看到的不完全一样。\n"
                "先跑 `text` 看原文，把要改的整句原样复制过来。"
            )
        if total > 1 and not args.all and not args.occurrence:
            shown = "、".join(f"{name} 第 {index} 段" for name, index, _ in hits[:4])
            raise Failed(
                f"{old!r} 出现了 {total} 次，说不清改哪一处（{shown}）。\n"
                "把原文写长一点只留一处，或者 --all 全改，"
                "或者 --occurrence N 指定第几处。"
            )
        want = None if args.all else (args.occurrence or 1)
        seen = 0
        p_tag = _tags(kind)[0]
        for name in package.text_parts():
            if want is not None and seen >= want:
                break
            root = package.elements(name)
            counter = [_next_revision_id(root)]
            changed = 0
            for paragraph in root.iter(p_tag):
                if want is not None and seen >= want:
                    break
                position = 0
                while True:
                    if flag == "replace":
                        nxt = replace_once(
                            paragraph,
                            kind,
                            old,
                            new,
                            position,
                            args.track,
                            args.author,
                            date,
                            counter,
                        )
                    else:
                        nxt = insert_after(
                            paragraph,
                            kind,
                            old,
                            new,
                            position,
                            args.track,
                            args.author,
                            date,
                            counter,
                        )
                    if nxt < 0:
                        break
                    position = nxt
                    seen += 1
                    changed += 1
                    if want is not None and seen >= want:
                        break
                    if flag == "replace" and not new:
                        break
            if changed:
                package.put(name, root)
                touched[name] = touched.get(name, 0) + changed
        if seen == 0:
            raise Failed(f"{old!r} 一次也没改到，停下来让你看一眼")

    package.save(dest)
    for name, count in touched.items():
        print(f"{name}：{count} 处")
    mode = "Word 修订" if args.track else "直接改（没有修订标记）"
    print(f"写出 {dest}（{mode}，署名 {args.author}）")
    if args.track:
        print(
            "接着跑 validate --base 原文件：它会确认「拒绝全部修订」能逐字回到原文档。"
        )
    return 0


def _unwrap(element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    index = list(parent).index(element)
    for child in reversed(list(element)):
        parent.insert(index, child)
    parent.remove(element)


def _reject(root) -> None:
    """Undo every revision: drop insertions, restore deletions."""
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.tag == INS:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
        elif el.tag == DEL:
            for node in el.iter(DEL_TEXT):
                node.tag = WQ + "t"
            _unwrap(el)


def _accept(root) -> None:
    """Apply every revision: keep insertions, drop deletions."""
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.tag == INS:
            _unwrap(el)
        elif el.tag == DEL:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)


def _live_text(root, kind: str) -> str:
    p_tag = _tags(kind)[0]
    return "\n".join(paragraph_text(p, kind) for p in root.iter(p_tag))


def _revision_elements(root) -> list:
    return [
        el for el in root.iter() if isinstance(el.tag, str) and el.tag in (INS, DEL)
    ]


def _first_diff(a: str, b: str, width: int = 60) -> list[str]:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            start = max(0, i - width // 2)
            return [
                f"第 {i} 个字符起不一样：",
                f"  原文档 …{a[start : i + width]}…",
                f"  新文档 …{b[start : i + width]}…",
            ]
    if len(a) != len(b):
        shorter = min(len(a), len(b))
        which = "原文档" if len(a) > len(b) else "新文档"
        rest = (a if len(a) > len(b) else b)[shorter : shorter + width]
        return [f"长度不同：{which}多出 {abs(len(a) - len(b))} 个字（{rest!r}）"]
    return []


def cmd_validate(args) -> int:
    target = Path(args.package)
    package = Package(target)
    kind = package.kind()
    problems: list[str] = []

    for name in package.names:
        if name.endswith(".xml") or name.endswith(".rels"):
            try:
                package.elements(name)
            except ET.XMLSyntaxError as exc:  # noqa: PERF203 - report every part
                problems.append(f"{name} 不是合法 XML：{exc}")
    print(
        f"{target}：{len(package.names)} 个部件，"
        + ("XML 全部解析成功" if not problems else "有解析失败的部件")
    )

    if not args.base:
        for name in package.text_parts():
            root = package.elements(name)
            revisions = _revision_elements(root)
            if revisions:
                authors = sorted(
                    {r.get(WQ + "author") or "（没写署名）" for r in revisions}
                )
                print(f"{name}：{len(revisions)} 处修订，署名 {'、'.join(authors)}")
        if problems:
            print("\n".join("问题：" + p for p in problems))
            return 1
        print("没给 --base，只检查了文件本身。要确认没动到原文，请给 --base 原文件。")
        return 0

    base = Package(Path(args.base))
    changed = [
        n for n in package.names if base.has(n) and base.blobs[n] != package.blobs[n]
    ]
    removed = [n for n in base.names if not package.has(n)]
    added = [n for n in package.names if not base.has(n)]
    if removed:
        problems.append("这些部件原文件里有、新文件里没了：" + "、".join(removed[:5]))
    print(
        f"改动过的部件：{'、'.join(changed) if changed else '（没有）'}"
        + (f"；新增 {len(added)} 个" if added else "")
    )

    for name in changed:
        if kind != "word" or not name.endswith(".xml"):
            continue
        base_root, new_root = base.elements(name), package.elements(name)

        rejected, accepted = copy.deepcopy(new_root), copy.deepcopy(new_root)
        _reject(rejected)
        _accept(accepted)

        original = _live_text(base_root, kind)
        after_reject = _live_text(rejected, kind)
        if after_reject != original:
            problems.append(
                f"{name}：拒绝全部修订之后正文和原文档对不上——"
                "有改动没被放进修订标记里。"
            )
            for line in _first_diff(original, after_reject):
                print("    " + line)
        else:
            print(f"{name}：拒绝全部修订 → 正文与原文档逐字相同 ✓")

        print(f"{name}：接受全部修订 →")
        for line in _live_text(accepted, kind).split("\n"):
            if line.strip():
                print(f"    {line}")

        for el in _revision_elements(new_root):
            author = el.get(WQ + "author")
            if not author:
                problems.append(f"{name}：有一处修订没写署名")
            elif args.author and author != args.author:
                problems.append(
                    f"{name}：有一处修订署名是 {author!r}，不是 {args.author!r}"
                )

    if problems:
        print("\n".join("问题：" + p for p in problems))
        return 1
    print("校验通过。")
    return 0


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="office.py", description="在已有的 Office 文件上做最小的、可核对的修改"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("unpack", help="把 Office 文件拆成目录，逐个部件改")
    p.add_argument("package")
    p.add_argument("directory")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", help="把目录装回 Office 文件")
    p.add_argument("directory")
    p.add_argument("output")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("text", help="打印正文，带段号")
    p.add_argument("package")
    p.add_argument("--part", help="只看某个部件，可只写文件名")
    p.add_argument(
        "--revisions", action="store_true", help="标出已有的修订：⟦+插入⟧ ⟦-删除⟧"
    )
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("edit", help="查找替换，默认写成 Word 修订")
    p.add_argument("package")
    p.add_argument("-o", "--output", help="输出文件；省略则覆盖原文件")
    p.add_argument("--replace", action="append", metavar="原文=新文本")
    p.add_argument(
        "--delete",
        action="append",
        metavar="原文",
        help="删掉这段（等于 --replace 原文=）",
    )
    p.add_argument(
        "--insert-after",
        action="append",
        metavar="锚点=新文本",
        help="在锚点文字后面插入，不动原有文字",
    )
    p.add_argument("--all", action="store_true", help="每一处都改")
    p.add_argument("--occurrence", type=int, metavar="N", help="只改第 N 处")
    p.add_argument("--author", default="Cheese", help="修订里署的名字")
    track = p.add_mutually_exclusive_group()
    track.add_argument(
        "--track",
        dest="track",
        action="store_true",
        default=True,
        help="写成修订标记（默认）",
    )
    track.add_argument(
        "--plain", dest="track", action="store_false", help="直接改，不留修订标记"
    )
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("validate", help="查文件完整性；给了 --base 就查「没动到原文」")
    p.add_argument("package")
    p.add_argument("--base", help="改动前的原文件")
    p.add_argument("--author", help="本次修订应当署的名字")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Failed as exc:
        print(f"office.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
