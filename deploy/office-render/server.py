"""What LibreOffice and OfficeCLI know about a document, over HTTP.

A Word report, a deck or a budget is the deliverable itself, and a deliverable
nobody can see without downloading it is most of the way to not having been
delivered. Browsers render PDF natively, so converting to PDF once, here, is
what puts every one of those formats on screen.

It is a separate service rather than a library inside the API because
LibreOffice installs about 800MB and expects a writable profile directory. A
room cannot install it (it has no root), and the backend image should not.

It answers three questions. Two are LibreOffice, which loads the document: what
it looks like (a PDF, for showing a deliverable on screen) and what its formulas
come to (a workbook, recalculated). The second is here rather than anywhere else
for the same reason as the first — nothing outside this image can evaluate a
spreadsheet.

The third is a web page, and it is OfficeCLI rather than LibreOffice. A PDF
draws the document and then stops: the shape of a chart, the fill behind a
header row and the fact that a paragraph is paragraph 7 all dissolve into
pixels, and a reader who wants to say "改这一格" has nothing to point with. The
page keeps all of it, on the elements themselves, which is what makes what a
reader clicks here mean the same thing to the agent that has to change it.

The service is READ ONLY with respect to the caller: it takes bytes, returns
bytes, and keeps nothing. Every request works in its own directory, which is
removed before the response is sent.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

#: What LibreOffice is asked to open. The suffix is passed through to the file it
#: writes into its working directory, because soffice decides the input filter
#: from the extension — an `.xlsx` saved as `document` converts as nothing.
SUPPORTED = {
    ".docx": "writer_pdf_Export",
    ".doc": "writer_pdf_Export",
    ".odt": "writer_pdf_Export",
    ".rtf": "writer_pdf_Export",
    ".pptx": "impress_pdf_Export",
    ".ppt": "impress_pdf_Export",
    ".odp": "impress_pdf_Export",
}

#: Measured on this image, one page of Chinese text with a table, a footnote and
#: four equations: 1.1s cold, 1.0s with the profile already written. Reusing a
#: profile buys ~0.1s, which is the reason this service starts a process per
#: request instead of supervising a resident one — the usual justification for
#: keeping soffice warm (unoserver and friends) is a cold start measured in
#: seconds, and it is not there.
#:
#: The timeout is for the other failure: soffice on a malformed file does not
#: crash, it waits. Without a bound the request hangs and the worker with it.
CONVERT_TIMEOUT_S = float(os.environ.get("OFFICE_RENDER_TIMEOUT", "60"))

#: LibreOffice allows ONE process per user profile, and the way it enforces that
#: is the problem: a second process sharing a profile exits quietly having
#: written nothing. Measured — five concurrent conversions against one shared
#: profile produced two PDFs and no error, while five with a profile each
#: produced five in 1.18s total. So every request gets its own, and the
#: semaphore exists only to stop a burst from spawning LibreOffices until the
#: box swaps.
MAX_CONCURRENT = int(os.environ.get("OFFICE_RENDER_CONCURRENT", "4"))

#: A bound on what one request may hand over, so a runaway upload cannot fill
#: the container's disk. Matches the platform's own artifact ceiling.
MAX_BYTES = int(os.environ.get("OFFICE_RENDER_MAX_BYTES", str(10 * 1024 * 1024)))

#: What /convert will turn into what. The pairs are listed rather than derived
#: because the interesting ones are the upgrades out of the pre-2007 binary
#: formats, which nothing in a room can read: those files are not zips, and the
#: alternative to converting them here is telling the user to go and do it in
#: Office himself.
#:
#: A target equal to its source is absent on purpose — that is /recalc, which
#: needs the profile seeding below, and routing it through here would quietly
#: skip it.
CONVERTIBLE = {
    ".doc": ("docx", "pdf"),
    ".rtf": ("docx", "pdf"),
    ".odt": ("docx", "pdf"),
    ".ppt": ("pptx", "pdf"),
    ".odp": ("pptx", "pdf"),
    ".xls": ("xlsx",),
    ".ods": ("xlsx",),
    ".docx": ("pdf",),
    ".pptx": ("pdf",),
    ".xlsx": ("pdf",),
}

#: What a spreadsheet arrives as, for /recalc. Only `.xlsx`, and deliberately
#: not `.xlsm`: recalculation writes the workbook back through the plain xlsx
#: filter, which drops the macros a `.xlsm` exists to carry — silently, with a
#: file of a normal size under the name it came in with.
RECALCULABLE = (".xlsx",)

#: LibreOffice trusts the value cached beside a formula and writes it straight
#: back — `OOXMLRecalcMode` is 1, "never recalculate on load". Measured: a
#: `SUM(A1:A2)` over 2 and 3 carrying a cached 999 converts to 999 again, while
#: the same cell with no cached value at all comes out as 5. So the failure is
#: exactly the one that has no symptom: the file opens, the formula is right,
#: and the number under it is the one from before the edit.
#:
#: There is no command-line switch for this; the setting reaches soffice only
#: through the profile it loads, and every request already gets a profile of its
#: own (see MAX_CONCURRENT). Seeding that profile with mode 0 recalculates on
#: load, which turns the 999 above into 5.
RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
 xmlns:xs="http://www.w3.org/2001/XMLSchema"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load">
<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>
</item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load">
<prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop>
</item>
</oor:items>
"""

#: What the web view can be built from. Deliberately narrower than `SUPPORTED`:
#: OfficeCLI reads the OOXML formats and nothing else, so a `.doc`, an `.odt` or
#: an `.xls` has a PDF and no page. Those are the formats a room upgrades with
#: `cheese convert` before working on them anyway.
HTML_RENDERABLE = (".docx", ".xlsx", ".pptx")

#: OfficeCLI's own bound, not `CONVERT_TIMEOUT_S`. Measured on this image: a
#: 19-paragraph Word file 3.5s, a 27-slide deck 2.7s, a 2000-row sheet 5.1s.
#: Slower than LibreOffice per document, and it grows with the document rather
#: than staying flat, so the limit is what a page may cost rather than what a
#: conversion may.
HTML_TIMEOUT_S = float(os.environ.get("OFFICE_HTML_TIMEOUT", "60"))

#: A page is markup, not a picture, and a sheet that is nothing on disk can be
#: hundreds of megabytes of `<tr>`: 2000 rows of 14 cells — a 127KB workbook —
#: came out at 3.0MB. Somewhere past this the reader's browser is the thing that
#: fails, and it fails by hanging rather than by saying so.
HTML_MAX_BYTES = int(os.environ.get("OFFICE_HTML_MAX_BYTES", str(8 * 1024 * 1024)))

#: Where the Dockerfile puts it. Named here so the health check and the refusal
#: below cannot drift from the path the image installs.
OFFICECLI = "officecli"

app = FastAPI(title="cheese office-render")
_slots = asyncio.Semaphore(MAX_CONCURRENT)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": shutil.which("soffice") is not None,
        # Reported separately rather than folded into `ok`: the two renderers are
        # independent, so an image with LibreOffice and no OfficeCLI is a working
        # PDF service with a web view that answers 503, and saying "unhealthy"
        # would take the whole container out of rotation over the second one.
        "html": shutil.which(OFFICECLI) is not None,
        "max_concurrent": MAX_CONCURRENT,
        "formats": sorted(SUPPORTED),
        "html_formats": sorted(HTML_RENDERABLE),
    }



def _convert(
    raw: bytes, suffix: str, target: str = "pdf", recalculate: bool = False
) -> bytes:
    """Run one conversion in a directory of its own, and return the result."""
    workdir = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        source = workdir / f"document{suffix}"
        source.write_bytes(raw)
        # The result goes in a directory of its own. Converting .xlsx to xlsx
        # would otherwise name its output exactly the source file, and soffice
        # will not write over the document it is reading — it exits 0 having
        # done nothing, and the file still sitting there reads as a success.
        outdir = workdir / "out"
        outdir.mkdir()
        profile = workdir / "profile"
        if recalculate:
            (profile / "user").mkdir(parents=True)
            (profile / "user" / "registrymodifications.xcu").write_text(
                RECALC_PROFILE, encoding="utf8"
            )
        # -env:UserInstallation is what makes concurrency work at all; see the
        # note on MAX_CONCURRENT for what sharing one costs.
        proc = subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to",
                target,
                "--outdir",
                str(outdir),
                str(source),
            ],
            capture_output=True,
            timeout=CONVERT_TIMEOUT_S,
        )
        out = outdir / f"document.{target}"
        if not out.exists():
            # soffice reports a refused document on stdout and still exits 0, so
            # the missing file is the signal, not the return code.
            detail = (proc.stderr or proc.stdout or b"").decode("utf8", "replace")
            raise RuntimeError(detail.strip()[:400] or "转换没有产出文件")
        return out.read_bytes()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class PageTooLarge(RuntimeError):
    """The page came out past what a browser will open without hanging."""


def _to_html(raw: bytes, suffix: str) -> bytes:
    """Run officecli over one document, in a directory of its own."""
    workdir = Path(tempfile.mkdtemp(prefix="html-"))
    try:
        source = workdir / f"document{suffix}"
        source.write_bytes(raw)
        out = workdir / "document.html"
        proc = subprocess.run(
            [OFFICECLI, "view", str(source), "html", "-o", str(out)],
            capture_output=True,
            timeout=HTML_TIMEOUT_S,
            cwd=workdir,
            # A .NET application keeps state under the user's home, and the
            # container's one home is shared by every request in flight — the
            # same shape of failure as two soffices sharing a profile, so it
            # gets the same answer.
            env={**os.environ, "HOME": str(workdir), "TMPDIR": str(workdir)},
        )
        if not out.exists():
            # As with soffice, the exit code is not the signal: officecli says
            # what it refused on stderr and the missing file is what tells us.
            detail = (proc.stderr or proc.stdout or b"").decode("utf8", "replace")
            raise RuntimeError(detail.strip()[:400] or "渲染没有产出文件")
        page = out.read_bytes()
        if len(page) > HTML_MAX_BYTES:
            raise PageTooLarge(
                f"这一份的网页视图超过 {HTML_MAX_BYTES // (1024 * 1024)}MB"
            )
        return page
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/render")
async def render(request: Request, suffix: str = "") -> Response:
    """Convert one document to PDF. `suffix` names the format, e.g. `.docx`.

    The document arrives as the raw request body rather than a multipart upload:
    there is exactly one file and no other field, and raw bytes save both sides a
    parser.
    """
    body = await request.body()
    suffix = suffix.lower().strip()
    if suffix not in SUPPORTED:
        return JSONResponse(
            {"ok": False, "error": f"不支持的格式 {suffix or '(未指明)'}"},
            status_code=400,
        )
    if not body:
        return JSONResponse({"ok": False, "error": "没有收到文件内容"}, status_code=400)
    if len(body) > MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"文件超过 {MAX_BYTES // (1024 * 1024)}MB"},
            status_code=413,
        )
    async with _slots:
        try:
            pdf = await asyncio.to_thread(_convert, body, suffix)
        except subprocess.TimeoutExpired:
            return JSONResponse(
                {"ok": False, "error": "转换超时"}, status_code=504
            )
        except Exception as exc:  # noqa: BLE001 — the message is the response
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return Response(content=pdf, media_type="application/pdf")


@app.post("/html")
async def html(request: Request, suffix: str = "") -> Response:
    """Render one document as a single self-contained HTML page.

    Sibling of `/render`, not a replacement: a reader who wants the page as it
    would print still gets the PDF. This answers the other question — where is
    the seventh paragraph, which cell is this — by handing back a page whose
    elements carry the addresses officecli's own edit commands accept.
    """
    body = await request.body()
    suffix = suffix.lower().strip()
    if suffix not in HTML_RENDERABLE:
        return JSONResponse(
            {"ok": False, "error": f"网页视图不支持 {suffix or '(未指明)'}"},
            status_code=400,
        )
    if not body:
        return JSONResponse({"ok": False, "error": "没有收到文件内容"}, status_code=400)
    if len(body) > MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"文件超过 {MAX_BYTES // (1024 * 1024)}MB"},
            status_code=413,
        )
    # Checked here rather than only in healthz: an image built without the
    # binary is still a working PDF service, and a reader who asked for the web
    # view deserves the reason rather than an empty page.
    if shutil.which(OFFICECLI) is None:
        return JSONResponse(
            {"ok": False, "error": "这个部署没有装网页渲染器"}, status_code=503
        )
    async with _slots:
        try:
            page = await asyncio.to_thread(_to_html, body, suffix)
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "error": "渲染超时"}, status_code=504)
        except PageTooLarge as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=413)
        except Exception as exc:  # noqa: BLE001 — the message is the response
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return Response(content=page, media_type="text/html; charset=utf-8")


@app.post("/convert")
async def convert(request: Request, suffix: str = "", to: str = "") -> Response:
    """Convert one document to another format. `suffix` is what arrives, `to`
    what to produce — e.g. `.doc` to `docx`.

    This is the upgrade path out of the pre-2007 binary formats, which are not
    zips and which nothing in a room can read or write. It is also how a room
    gets a page of a Word file as an image for its own inspection: convert to
    pdf here, rasterise there.
    """
    body = await request.body()
    suffix = suffix.lower().strip()
    to = to.lower().strip().lstrip(".")
    allowed = CONVERTIBLE.get(suffix)
    if not allowed:
        return JSONResponse(
            {"ok": False, "error": f"不能转换 {suffix or '(未指明)'}"}, status_code=400
        )
    if to not in allowed:
        return JSONResponse(
            {
                "ok": False,
                "error": f"{suffix} 只能转成 {'、'.join(allowed)}，收到 {to or '(未指明)'}",
            },
            status_code=400,
        )
    if not body:
        return JSONResponse({"ok": False, "error": "没有收到文件内容"}, status_code=400)
    if len(body) > MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"文件超过 {MAX_BYTES // (1024 * 1024)}MB"},
            status_code=413,
        )
    async with _slots:
        try:
            made = await asyncio.to_thread(_convert, body, suffix, to)
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "error": "转换超时"}, status_code=504)
        except Exception as exc:  # noqa: BLE001 — the message is the response
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return Response(content=made, media_type="application/octet-stream")


@app.post("/recalc")
async def recalc(request: Request, suffix: str = ".xlsx") -> Response:
    """Recompute every formula in one spreadsheet, and return the workbook.

    A sheet written by a program carries formulas with no result under them, and
    a sheet edited in place carries the result from before the edit. Both open
    without complaint and both show the reader a number that is not the answer.
    It is a separate endpoint rather than an argument to /render because the
    caller wants the workbook back, not a picture of it.

    Cells that cannot be computed come back as Excel error values (`#DIV/0!` and
    friends) inside the workbook, so the caller reads them from what it
    receives — this service still keeps nothing, and still says nothing about
    the content it converted.
    """
    body = await request.body()
    suffix = suffix.lower().strip()
    if suffix not in RECALCULABLE:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    f"只能重算 {'、'.join(RECALCULABLE)}，"
                    f"收到 {suffix or '(未指明)'}"
                ),
            },
            status_code=400,
        )
    if not body:
        return JSONResponse({"ok": False, "error": "没有收到文件内容"}, status_code=400)
    if len(body) > MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"文件超过 {MAX_BYTES // (1024 * 1024)}MB"},
            status_code=413,
        )
    async with _slots:
        try:
            book = await asyncio.to_thread(_convert, body, suffix, "xlsx", True)
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "error": "重算超时"}, status_code=504)
        except Exception as exc:  # noqa: BLE001 — the message is the response
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return Response(
        content=book,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
