"""A deck's speaker notes, one entry per slide, in the order the deck shows them.

The notes are not on the slides. They live in their own parts
(``ppt/notesSlides/notesSlideN.xml``), and **the number in that name is not the
slide number**: which slide a notes part belongs to is written in the slide's own
relationships (``ppt/slides/_rels/slideN.xml.rels``). So this walks
``ppt/presentation.xml``'s ``sldId`` list and follows each slide's own
relationship, rather than counting from 1 and hoping.

A notes page carries more than what the speaker typed: the slide number, the
footer and the date are placeholders of their own (``<p:ph type="sldNum">`` and
friends), and they are the same ``<a:t>`` text as the notes. Taking every run
would put the page number in as the first line of the notes, so the placeholder
kind is read first and those shapes are skipped.

Text only. Where a box sat on the notes page means nothing once it is read, and
there is no "original layout" to restore — in PowerPoint those boxes are just
paragraphs here.

Why this is here and not in the browser: the panel is handed the *converted PDF*,
so the notes are the one thing it cannot derive from what it has. Reading them
client-side would mean downloading the original deck a second time — up to the
200MB a file may be — to recover a few kilobytes of text. The bytes are already
on this side, being converted; this is the cheap half of that same read.
"""

from __future__ import annotations

import posixpath
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

_PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

#: The three placeholders that are chrome rather than something a speaker wrote.
#: A `<p:ph/>` with no `type` is the body placeholder, which is the notes.
_CHROME_PLACEHOLDERS = frozenset({"sldNum", "ftr", "dt"})


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """id → part path, for one part's own relationships file. Missing → empty.

    `Target` is written relative to the part that owns the rels, so it is joined
    against that part's directory and then normalized: the same file uses both
    `slides/slide1.xml` (from presentation) and `../notesSlides/notesSlide1.xml`
    (from a slide).
    """
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in z.namelist():
        return {}
    root = ET.fromstring(z.read(rels_name))
    out: dict[str, str] = {}
    for rel in root.iter(f"{{{_PKG_REL}}}Relationship"):
        key, target = rel.get("Id"), rel.get("Target")
        if key and target and rel.get("TargetMode") != "External":
            out[key] = posixpath.normpath(posixpath.join(folder, target))
    return out


def _notes_text(part: str, raw: bytes) -> str:
    """What the speaker typed on one notes page, a paragraph per line."""
    root = ET.fromstring(raw)
    lines: list[str] = []
    for shape in root.iter(f"{{{_PML}}}sp"):
        kind = shape.find(f".//{{{_PML}}}ph")
        if kind is not None and kind.get("type") in _CHROME_PLACEHOLDERS:
            continue
        for paragraph in shape.iter(f"{{{_DML}}}p"):
            text = "".join(t.text or "" for t in paragraph.iter(f"{{{_DML}}}t")).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def slide_notes(raw: bytes) -> list[str] | None:
    """One entry per slide, in show order; empty string where a slide has none.

    `None` when the bytes are not a deck this can read — the caller says "this
    file has no notes to show" rather than showing an empty list as if the deck
    were note-free.
    """
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            names = set(z.namelist())
            if "ppt/presentation.xml" not in names:
                return None
            deck = ET.fromstring(z.read("ppt/presentation.xml"))
            slides = _rels(z, "ppt/presentation.xml")
            out: list[str] = []
            for slide_id in deck.iter(f"{{{_PML}}}sldId"):
                slide = slides.get(
                    slide_id.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    or ""
                )
                if not slide or slide not in names:
                    out.append("")
                    continue
                # The notes part is found through this slide's own rels. Two
                # slides sharing one notes page is legal, and then both find it
                # the same way — nothing here assumes a part is used once.
                notes_part = next(
                    (
                        target
                        for target in _rels(z, slide).values()
                        if posixpath.basename(posixpath.dirname(target))
                        == "notesSlides"
                    ),
                    None,
                )
                if not notes_part or notes_part not in names:
                    out.append("")
                    continue
                try:
                    out.append(_notes_text(notes_part, z.read(notes_part)))
                except ET.ParseError:
                    out.append("")
            return out
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None
