"""A deck's speaker notes, one per slide.

The two things worth pinning are the two that are easy to get wrong and hard to
notice: the notes part's number is **not** the slide number (which slide owns
`notesSlide3.xml` is only written in that slide's rels), and a notes page carries
the slide number, footer and date as placeholders of their own — reading every
run puts the page number in as the first line of the notes.
"""

from __future__ import annotations

import io
import zipfile

from app.domain.documents.notes import NoteParagraph, NoteRun, deck_notes, slide_notes

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buffer.getvalue()


def _rels(*rows: tuple[str, str]) -> str:
    body = "".join(
        f'<Relationship Id="{rid}" Target="{target}"/>' for rid, target in rows
    )
    return f'<Relationships xmlns="{PKG}">{body}</Relationships>'


def _shape(text: str, kind: str | None = None) -> str:
    """One shape's text. `kind` is the placeholder type PowerPoint writes, e.g.
    `sldNum` for the page-number box; omitted means the body placeholder."""
    ph = f'<p:ph type="{kind}"/>' if kind else "<p:ph/>"
    return (
        f"<p:sp><p:nvSpPr><p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:txBody>"
        f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _notes(*shapes: str) -> str:
    return (
        f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        + "".join(shapes)
        + "</p:spTree></p:cSld></p:notes>"
    )


def _slide(hidden: bool = False) -> str:
    show = ' show="0"' if hidden else ""
    return (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}"{show}><p:cSld><p:spTree/></p:cSld></p:sld>'
    )


def deck(
    *,
    notes: dict[int, str | None],
    slide_count: int,
    numbered: dict[int, int] | None = None,
    hidden: frozenset[int] = frozenset(),
    body: dict[int, str] | None = None,
):
    """A deck whose notes parts are named by `numbered` (slide → notes file
    number), so page order and file numbering can be made to disagree.

    `notes` maps a slide number (1-based) to its note text; a slide absent from
    it has no notes part at all. `None` for a slide means it has a notes part
    that holds nothing.
    """
    ids = "".join(
        f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in range(1, slide_count + 1)
    )
    parts = {
        "[Content_Types].xml": TYPES,
        "ppt/presentation.xml": (
            f'<p:presentation xmlns:p="{P}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<p:sldIdLst>{ids}</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": _rels(
            *((f"rId{n}", f"slides/slide{n}.xml") for n in range(1, slide_count + 1))
        ),
    }
    for n in range(1, slide_count + 1):
        parts[f"ppt/slides/slide{n}.xml"] = _slide(n in hidden)
        rows: list[tuple[str, str]] = []
        if n in notes:
            number = (numbered or {}).get(n, n)
            rows.append((f"n{n}", f"../notesSlides/notesSlide{number}.xml"))
            parts[f"ppt/notesSlides/notesSlide{number}.xml"] = _notes(
                # The page number box comes first, exactly as PowerPoint orders
                # the tree — this is what leaks in if it is not skipped.
                _shape(str(n), "sldNum"),
                (body or {}).get(n) or _shape(notes[n] or ""),
            )
        parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(*rows)
    return _zip(parts)


def test_notes_come_back_one_per_slide_in_show_order():
    raw = deck(
        slide_count=4,
        notes={1: "先讲结论。", 2: "时间节点那一段停一下。", 4: "收尾，留提问。"},
    )
    assert slide_notes(raw) == [
        "先讲结论。",
        "时间节点那一段停一下。",
        "",
        "收尾，留提问。",
    ]


def test_a_page_number_is_not_mistaken_for_the_notes():
    raw = deck(slide_count=1, notes={1: "只有这一句。"})
    assert slide_notes(raw) == ["只有这一句。"]


def test_the_notes_part_number_does_not_decide_which_slide_it_belongs_to():
    # Slide 2's notes live in notesSlide3.xml and slide 3's in notesSlide2.xml:
    # counting files 1..N would swap these two.
    raw = deck(
        slide_count=3,
        notes={2: "第二页的备注", 3: "第三页的备注"},
        numbered={2: 3, 3: 2},
    )
    assert slide_notes(raw) == ["", "第二页的备注", "第三页的备注"]


def test_a_notes_page_with_nothing_on_it_is_not_a_missing_page():
    raw = deck(slide_count=2, notes={1: None, 2: "第二页有话说"})
    assert slide_notes(raw) == ["", "第二页有话说"]


def test_a_slide_with_no_notes_part_is_empty_and_the_rest_still_reads():
    raw = deck(slide_count=3, notes={3: "只有第三页有备注"})
    assert slide_notes(raw) == ["", "", "只有第三页有备注"]


def test_a_file_that_is_not_a_deck_has_no_notes_to_show():
    assert slide_notes(b"not a zip at all") is None
    assert slide_notes(_zip({"[Content_Types].xml": TYPES})) is None


def test_a_hidden_slide_keeps_its_place_and_says_it_is_hidden():
    # PowerPoint numbers a hidden slide and keeps its notes; the preview shows it
    # marked. Dropping it here would put slide 3's notes under slide 2.
    raw = deck(slide_count=3, notes={2: "备用页", 3: "第三页"}, hidden=frozenset({2}))
    read = deck_notes(raw)
    assert read is not None
    assert [slide.hidden for slide in read] == [False, True, False]
    assert [slide.text for slide in read] == ["", "备用页", "第三页"]


def test_outline_level_bullets_and_emphasis_survive():
    body = (
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:txBody>'
        '<a:p><a:r><a:t>先讲</a:t></a:r><a:r><a:rPr b="1"/><a:t>结论</a:t></a:r></a:p>'
        '<a:p><a:pPr lvl="1"><a:buChar char="•"/></a:pPr><a:r><a:rPr i="1"/><a:t>停一下</a:t></a:r></a:p>'
        '<a:p><a:pPr><a:buAutoNum type="arabicPeriod"/></a:pPr><a:r><a:t>第一点</a:t></a:r>'
        "<a:br/><a:r><a:t>换行</a:t></a:r></a:p>"
        "<a:p><a:r><a:t>   </a:t></a:r></a:p>"
        "</p:txBody></p:sp>"
    )
    read = deck_notes(deck(slide_count=1, notes={1: None}, body={1: body}))
    assert read is not None
    assert read[0].paragraphs == (
        NoteParagraph((NoteRun("先讲"), NoteRun("结论", bold=True))),
        NoteParagraph((NoteRun("停一下", italic=True),), level=1, bullet="dot"),
        NoteParagraph(
            (NoteRun("第一点"), NoteRun("\n"), NoteRun("换行")), bullet="number"
        ),
    )
    # The plain form is what the old field carries, a paragraph per line.
    assert slide_notes(deck(slide_count=1, notes={1: None}, body={1: body})) == [
        "先讲结论\n停一下\n第一点\n换行"
    ]
