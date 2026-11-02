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

What survives of the formatting is what a speaker reads by: paragraphs, their
outline level, bullets, bold and italic. Where a box sat on the notes page means
nothing once it is read, so the boxes are just paragraphs here.

Each slide also says whether it is hidden (``show="0"``). The converted PDF
keeps hidden slides (``deploy/office-render``), so notes and pages line up one
to one, numbered the way PowerPoint numbers them.

Why this is here and not in the browser: the panel is handed the *converted PDF*,
so the notes are the one thing it cannot derive from what it has. Reading them
client-side would mean downloading the original deck a second time — up to the
200MB a file may be — to recover a few kilobytes of text. The bytes are already
on this side, being converted; this is the cheap half of that same read.
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
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


@dataclass(frozen=True)
class NoteRun:
    """A stretch of one paragraph in one style. Only bold and italic survive:
    they carry emphasis a speaker reads by, while colour and size are layout and
    mean nothing once the box is gone."""

    text: str
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class NoteParagraph:
    """One paragraph of a notes page. ``level`` is PowerPoint's outline level
    (0 is the outermost), and ``bullet`` says what marks the line: a bullet
    character, a running number, or nothing."""

    runs: tuple[NoteRun, ...]
    level: int = 0
    bullet: Literal["dot", "number"] | None = None

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True)
class SlideNotes:
    """What the deck says about one slide besides the slide itself.

    ``hidden`` is the slide's own ``show="0"``: PowerPoint keeps a hidden slide
    in the deck, numbered, and skips it only when presenting. The preview does
    the same, so a reader's "第 3 页" and the author's are one page.
    """

    hidden: bool
    paragraphs: tuple[NoteParagraph, ...]

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


def _flag(props: ET.Element | None, name: str) -> bool:
    return props is not None and props.get(name) in ("1", "true")


def _paragraph(paragraph: ET.Element) -> NoteParagraph | None:
    """One ``<a:p>``, or None when it holds no text.

    A line break inside a paragraph (``<a:br/>``) stays a line break, and a field
    (``<a:fld>``, a date typed into the notes) reads as the text it shows.
    """
    runs: list[NoteRun] = []
    for child in paragraph:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in ("r", "fld"):
            text = "".join(t.text or "" for t in child.iter(f"{{{_DML}}}t"))
            props = child.find(f"{{{_DML}}}rPr")
            if text:
                runs.append(NoteRun(text, _flag(props, "b"), _flag(props, "i")))
        elif tag == "br":
            runs.append(NoteRun("\n"))
    if not "".join(run.text for run in runs).strip():
        return None
    # Blanks around the whole paragraph are layout, not words.
    first = runs[0]
    runs[0] = NoteRun(first.text.lstrip(), first.bold, first.italic)
    last = runs[-1]
    runs[-1] = NoteRun(last.text.rstrip(), last.bold, last.italic)
    runs = [run for run in runs if run.text]
    props = paragraph.find(f"{{{_DML}}}pPr")
    level = 0
    bullet: Literal["dot", "number"] | None = None
    if props is not None:
        try:
            level = max(0, min(8, int(props.get("lvl") or 0)))
        except ValueError:
            level = 0
        # Only what the paragraph itself says. A notes master could give its
        # body bullets too, but PowerPoint's own masters do not, and following
        # that inheritance for the rare one that does is not worth a guess.
        if props.find(f"{{{_DML}}}buAutoNum") is not None:
            bullet = "number"
        elif (
            props.find(f"{{{_DML}}}buChar") is not None
            or props.find(f"{{{_DML}}}buBlip") is not None
        ):
            bullet = "dot"
    return NoteParagraph(tuple(runs), level, bullet)


def _notes_paragraphs(raw: bytes) -> tuple[NoteParagraph, ...]:
    """What the speaker typed on one notes page, paragraph by paragraph."""
    root = ET.fromstring(raw)
    out: list[NoteParagraph] = []
    for shape in root.iter(f"{{{_PML}}}sp"):
        kind = shape.find(f".//{{{_PML}}}ph")
        if kind is not None and kind.get("type") in _CHROME_PLACEHOLDERS:
            continue
        for paragraph in shape.iter(f"{{{_DML}}}p"):
            read = _paragraph(paragraph)
            if read is not None:
                out.append(read)
    return tuple(out)


def _is_hidden(raw: bytes) -> bool:
    return ET.fromstring(raw).get("show") in ("0", "false")


def deck_notes(raw: bytes) -> list[SlideNotes] | None:
    """One entry per slide, in show order, hidden slides included.

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
            out: list[SlideNotes] = []
            for slide_id in deck.iter(f"{{{_PML}}}sldId"):
                slide = slides.get(
                    slide_id.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    or ""
                )
                if not slide or slide not in names:
                    out.append(SlideNotes(False, ()))
                    continue
                try:
                    hidden = _is_hidden(z.read(slide))
                except ET.ParseError:
                    hidden = False
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
                    out.append(SlideNotes(hidden, ()))
                    continue
                try:
                    paragraphs = _notes_paragraphs(z.read(notes_part))
                except ET.ParseError:
                    paragraphs = ()
                out.append(SlideNotes(hidden, paragraphs))
            return out
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None


def slide_notes(raw: bytes) -> list[str] | None:
    """The same notes as plain text, a paragraph per line; see `deck_notes`."""
    read = deck_notes(raw)
    return None if read is None else [slide.text for slide in read]
