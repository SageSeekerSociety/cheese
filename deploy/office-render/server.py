"""What LibreOffice knows about a document, behind two HTTP endpoints.

A Word report, a deck or a budget is the deliverable itself, and a deliverable
nobody can see without downloading it is most of the way to not having been
delivered. Browsers render PDF natively, so converting to PDF once, here, is
what puts every one of those formats on screen.

It is a separate service rather than a library inside the API because
LibreOffice installs about 800MB and expects a writable profile directory. The
sandbox image cannot carry it (that image is other people's base, and a room has
no root to install into), and the backend image should not.

It answers two questions, both by loading the document in LibreOffice: what it
looks like (a PDF, for showing a deliverable on screen) and what its formulas
come to (a workbook, recalculated). The second is here rather than anywhere
else for the same reason as the first — nothing outside this image can evaluate
a spreadsheet.

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

app = FastAPI(title="cheese office-render")
_slots = asyncio.Semaphore(MAX_CONCURRENT)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": shutil.which("soffice") is not None,
        "max_concurrent": MAX_CONCURRENT,
        "formats": sorted(SUPPORTED),
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
