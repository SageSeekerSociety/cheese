#!/usr/bin/env python3
"""Read a template's structure, and fill its 【占位】 without touching the rest.

    python3 template.py inspect 模板.docx
    python3 template.py fill 模板.docx -o 新文件.docx --values 填写.json

`inspect` answers "what is this file made of": for Word the styles each
paragraph uses, the tables and the placeholders; for slides each page's layout
and text boxes; for a workbook its sheets, header rows, formulas and charts. It
also names what this toolchain cannot carry into a new document (SmartArt,
content controls, pivot tables, macros …), so that is said up front rather than
discovered when the result comes back without it.

`fill` writes a copy with each placeholder replaced by its value, editing the
text nodes in the package itself — styles, numbering, headers, charts are the
template's bytes, untouched. A placeholder that is split across several runs,
or left without a value, is reported and the exit code is 1: a document still
saying 【报告标题】 is not finished.

Needs nothing installed: it reads and writes the zip package directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

PLACEHOLDER = re.compile(r"【[^【】]*】")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: Parts whose text a reader sees, by format.
_TEXT_PARTS = {
    "docx": re.compile(r"word/(document|header\d*|footer\d*)\.xml$"),
    "pptx": re.compile(r"ppt/(slides/slide\d+|slideLayouts/slideLayout\d+)\.xml$"),
    "xlsx": re.compile(r"xl/(sharedStrings|worksheets/sheet\d+)\.xml$"),
}
_TEXT_TAGS = {"docx": f"{W}t", "pptx": f"{A}t", "xlsx": f"{S}t"}

#: Things in a package this toolchain cannot rebuild, keyed by what gives them away.
_UNSUPPORTED = {
    "docx": [
        (r"word/diagrams/", "SmartArt 图形"),
        (r"<w:sdt[ >]", "内容控件（表单域）"),
        (r"<w:fldSimple|<w:instrText", "域代码（目录、页码、交叉引用）"),
        (r"<wps:txbx|<v:textbox", "文本框"),
        (r"word/embeddings/", "嵌入对象（OLE）"),
    ],
    "pptx": [
        (r"ppt/diagrams/", "SmartArt 图形"),
        (r"ppt/charts/", "图表（只能改标签文字，数据要在编辑器里改）"),
        (r"ppt/media/.*\.(mp4|mov|wmv|avi)$", "视频"),
        (r"ppt/embeddings/", "嵌入对象（OLE）"),
    ],
    "xlsx": [
        (r"xl/pivotTables/", "数据透视表"),
        (r"xl/vbaProject\.bin", "宏"),
        (r"xl/externalLinks/", "外部链接"),
        (r"xl/slicers/", "切片器"),
    ],
}


class Failed(Exception):
    pass


def _kind(path: str) -> str:
    kind = PurePosixPath(path).suffix.lower().lstrip(".")
    if kind not in _TEXT_PARTS:
        raise Failed(f"只支持 .docx / .pptx / .xlsx，这是 .{kind}")
    return kind


def _open(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise Failed(f"打不开 {path}：{exc}") from exc


def _unsupported(kind: str, book: zipfile.ZipFile) -> list[str]:
    names = book.namelist()
    found = []
    for pattern, label in _UNSUPPORTED[kind]:
        rx = re.compile(pattern)
        if any(rx.search(n) for n in names):
            found.append(label)
            continue
        if pattern.startswith("<"):
            for name in names:
                if _TEXT_PARTS[kind].search(name) and rx.search(
                    book.read(name).decode("utf-8", "replace")
                ):
                    found.append(label)
                    break
    return found


def _placeholders(kind: str, book: zipfile.ZipFile) -> list[str]:
    seen: list[str] = []
    for name in book.namelist():
        if not _TEXT_PARTS[kind].search(name):
            continue
        root = ElementTree.fromstring(book.read(name))
        text = "".join(t.text or "" for t in root.iter(_TEXT_TAGS[kind]))
        for hit in PLACEHOLDER.findall(text):
            if hit not in seen:
                seen.append(hit)
    return seen


def _inspect_docx(book: zipfile.ZipFile) -> list[str]:
    lines = []
    names: dict[str, str] = {}
    if "word/styles.xml" in book.namelist():
        for style in ElementTree.fromstring(book.read("word/styles.xml")).iter(
            f"{W}style"
        ):
            label = style.find(f"{W}name")
            if label is not None:
                names[style.get(f"{W}styleId") or ""] = label.get(f"{W}val") or ""
    root = ElementTree.fromstring(book.read("word/document.xml"))
    body = root.find(f"{W}body")
    styles_used: dict[str, int] = {}
    for block in list(body) if body is not None else []:
        if block.tag == f"{W}p":
            style = block.find(f"{W}pPr/{W}pStyle")
            ident = style.get(f"{W}val") or "" if style is not None else "Normal"
            name = names.get(ident, ident)
            text = "".join(t.text or "" for t in block.iter(f"{W}t")).strip()
            styles_used[name] = styles_used.get(name, 0) + 1
            if text:
                lines.append(f"  [{name}] {text[:60]}")
        elif block.tag == f"{W}tbl":
            rows = block.findall(f"{W}tr")
            cols = len(rows[0].findall(f"{W}tc")) if rows else 0
            head = [
                "".join(t.text or "" for t in c.iter(f"{W}t")).strip()
                for c in (rows[0].findall(f"{W}tc") if rows else [])
            ]
            lines.append(f"  [表格 {len(rows)}×{cols}] 表头：{' | '.join(head)}")
    lines.append(
        "用到的段落样式：" + "、".join(f"{k}×{v}" for k, v in styles_used.items())
    )
    return lines


def _inspect_pptx(book: zipfile.ZipFile) -> list[str]:
    lines = []
    slides = sorted(
        (n for n in book.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),  # type: ignore[union-attr]
    )
    for i, name in enumerate(slides, 1):
        root = ElementTree.fromstring(book.read(name))
        boxes = []
        for shape in root.iter(f"{P}sp"):
            text = " / ".join(
                "".join(t.text or "" for t in para.iter(f"{A}t"))
                for para in shape.iter(f"{A}p")
            ).strip(" /")
            if text:
                boxes.append(text[:50])
        pictures = len(list(root.iter(f"{P}pic")))
        extra = f"，{pictures} 张图片" if pictures else ""
        lines.append(f"  第 {i} 页：{' ‖ '.join(boxes)}{extra}")
    return lines


def _inspect_xlsx(book: zipfile.ZipFile) -> list[str]:
    lines = []
    shared = []
    if "xl/sharedStrings.xml" in book.namelist():
        root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{S}t")) for si in root]
    wb = ElementTree.fromstring(book.read("xl/workbook.xml"))
    sheets = [s.get("name") for s in wb.iter(f"{S}sheet")]
    parts = sorted(
        (n for n in book.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),  # type: ignore[union-attr]
    )
    for name, part in zip(sheets, parts, strict=False):
        root = ElementTree.fromstring(book.read(part))
        formulas = len(list(root.iter(f"{S}f")))
        head = []
        first = root.find(f"{S}sheetData/{S}row")
        for cell in first if first is not None else []:
            value = cell.find(f"{S}v")
            if cell.get("t") == "inlineStr":
                head.append("".join(t.text or "" for t in cell.iter(f"{S}t")))
            elif cell.get("t") == "s" and value is not None and value.text:
                head.append(shared[int(value.text)])
            elif value is not None and value.text:
                head.append(value.text)
        dim = root.find(f"{S}dimension")
        span = dim.get("ref") if dim is not None else "?"
        lines.append(
            f"  [{name}] 范围 {span}，{formulas} 个公式；首行：{' | '.join(head)}"
        )
    charts = [n for n in book.namelist() if n.startswith("xl/charts/chart")]
    lines.append(f"图表：{len(charts)} 个")
    return lines


def cmd_inspect(args) -> int:
    kind = _kind(args.file)
    with _open(args.file) as book:
        body = {
            "docx": _inspect_docx,
            "pptx": _inspect_pptx,
            "xlsx": _inspect_xlsx,
        }[kind](book)
        holes = _placeholders(kind, book)
        missing = _unsupported(kind, book)
    print(f"{args.file}（{kind}）")
    print("\n".join(body))
    print(
        "占位：" + ("、".join(holes) if holes else "（没有【】占位，按结构和样式参照）")
    )
    if missing:
        print("这套工具带不过去、要照原样保留或告诉用户的：" + "、".join(missing))
    return 0


#: A text node in any of the three formats, matched in the raw bytes. Filling
#: edits only what sits between the tags, so every other byte of the part —
#: namespace declarations included, which a parse-and-reserialise would drop and
#: Word then refuses to open — stays the template's.
_TEXT_NODE = {
    "docx": re.compile(rb"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)"),
    "pptx": re.compile(rb"(<a:t(?:\s[^>]*)?>)([^<]*)(</a:t>)"),
    "xlsx": re.compile(rb"(<t(?:\s[^>]*)?>)([^<]*)(</t>)"),
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fill_part(kind: str, xml: bytes, values: dict[str, str], used: set[str]) -> bytes:
    def one(match: re.Match[bytes]) -> bytes:
        text = match.group(2).decode("utf-8")
        if "【" not in text:
            return match.group(0)
        for key, value in values.items():
            if key in text:
                text = text.replace(key, _escape(value))
                used.add(key)
        return match.group(1) + text.encode("utf-8") + match.group(3)

    return _TEXT_NODE[kind].sub(one, xml)


def cmd_fill(args) -> int:
    kind = _kind(args.template)
    if PurePosixPath(args.output).suffix.lower().lstrip(".") != kind:
        raise Failed(f"输出要和模板同一种格式（.{kind}）")
    try:
        values = json.loads(open(args.values, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise Failed(f"读不了填写内容 {args.values}：{exc}") from exc
    if not isinstance(values, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in values.items()
    ):
        raise Failed('填写内容要是 {"【占位】": "内容"} 这样的 JSON 对象')
    used: set[str] = set()
    with (
        _open(args.template) as source,
        zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if _TEXT_PARTS[kind].search(item.filename):
                data = _fill_part(kind, data, values, used)
            out.writestr(item, data)
    with _open(args.output) as book:
        left = _placeholders(kind, book)
    unused = [k for k in values if k not in used]
    print(f"写出 {args.output}：替换了 {len(used)} 个占位。")
    status = 0
    if unused:
        print("没找到、没替换的：" + "、".join(unused))
        print(
            "  多半是这段占位在文件里被拆成了几段文字，或者写错了。先 inspect 看原文。"
        )
        status = 1
    if left and not args.allow_leftover:
        print("还留着没填的占位：" + "、".join(left))
        status = 1
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读模板的结构，填模板的占位")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser(
        "inspect", help="这个文件由哪些段落样式、表格、页面、工作表构成"
    )
    pi.add_argument("file")
    pf = sub.add_parser("fill", help="按 JSON 把【占位】换成内容，写出一份新文件")
    pf.add_argument("template")
    pf.add_argument("-o", "--output", required=True)
    pf.add_argument("--values", required=True, help='JSON：{"【占位】": "内容"}')
    pf.add_argument(
        "--allow-leftover", action="store_true", help="允许留下没填的占位（草稿）"
    )
    args = parser.parse_args(argv)
    try:
        return {"inspect": cmd_inspect, "fill": cmd_fill}[args.cmd](args)
    except Failed as exc:
        print(f"template.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
