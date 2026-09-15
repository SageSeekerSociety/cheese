"""Office documents to PDF, behind one HTTP endpoint.

A Word report, a deck or a budget is the deliverable itself, and a deliverable
nobody can see without downloading it is most of the way to not having been
delivered. Browsers render PDF natively, so converting to PDF once, here, is
what puts every one of those formats on screen.

It is a separate service rather than a library inside the API because
LibreOffice installs about 800MB and expects a writable profile directory. The
sandbox image cannot carry it (that image is other people's base, and a room has
no root to install into), and the backend image should not.

The service is READ ONLY with respect to the caller: it takes bytes, returns a
PDF, and keeps nothing. Every request works in its own directory, which is
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

app = FastAPI(title="cheese office-render")
_slots = asyncio.Semaphore(MAX_CONCURRENT)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": shutil.which("soffice") is not None,
        "max_concurrent": MAX_CONCURRENT,
        "formats": sorted(SUPPORTED),
    }


def _convert(raw: bytes, suffix: str) -> bytes:
    """Run one conversion in a directory of its own, and return the PDF."""
    workdir = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        source = workdir / f"document{suffix}"
        source.write_bytes(raw)
        # -env:UserInstallation is what makes concurrency work at all; see the
        # note on MAX_CONCURRENT for what sharing one costs.
        proc = subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file://{workdir / 'profile'}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workdir),
                str(source),
            ],
            capture_output=True,
            timeout=CONVERT_TIMEOUT_S,
        )
        out = workdir / "document.pdf"
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
