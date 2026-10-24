"""A document as a PDF.

The document is drawn by the browser that has it open — its styles, its blocks,
its pictures — and the page that browser makes is handed here only to be
printed. The printing is the platform's browser (`deploy/browser-render`), which
turns an HTML page into A4 exactly as it does for `cheese check --pdf` and a
one-page report. Nothing is re-parsed on the way: the printout cannot disagree
with what the reader was looking at.

The page is the caller's own, so the only door is the document's: whoever may
read it may print it (`reach`). That is also why the page may be large — it is
text plus the pictures in it — and why it is printed by the same service that
renders untrusted pages for agents: its egress reaches the public internet and
nothing else, and it runs the page with no credential behind it.
"""

import base64
import uuid

import httpx
from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.doc_access import reach
from app.api.inert_headers import INERT_HEADERS
from app.api.request_deps import DbSession
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.core.errors import SystemBusyError
from app.core.sentences import say

router = APIRouter(prefix="/documents", tags=["documents"])

#: A page is one A4 with the printer's own margins: enough for the eye to rest,
#: and the same margins `cheese check --pdf` uses for a report.
PDF_MARGIN = "16mm 18mm"

#: One page may weigh this much. It is the document's text plus the pictures in
#: it; past this the printing browser is the thing that fails, and it fails
#: slowly rather than saying so.
MAX_PAGE_CHARS = 24_000_000

#: Laying out a long document before the first page comes out takes longer than
#: inspecting one page at a time.
PRINT_TIMEOUT_S = 120.0

#: A renderer older than this route answers 404 or 405. That is the
#: deployment's state, not a problem with the document — the same reading as in
#: `app.domain.documents.convert`.
_NOT_THERE = (404, 405)


class DocumentPdfIn(BaseModel):
    """The page, as the reader's browser drew it."""

    html: str = Field(min_length=1, max_length=MAX_PAGE_CHARS)


@router.post(
    "/{document_id}/pdf",
    dependencies=[ROUTE_DECIDES],
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The page."}},
)
async def document_pdf(
    document_id: uuid.UUID,
    body: DocumentPdfIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> Response:
    """Print the document the caller can read."""
    await reach(db, resolver, document_id)
    endpoint = settings.fetch_browser_endpoint
    if not endpoint:
        raise SystemBusyError(say("docPrintUnavailable"))

    answer = await _print(endpoint, body.html)
    made = base64.b64decode(answer.get("pdf_b64") or "")
    # Every PDF opens with its own signature; anything else is an error page
    # arriving under a PDF's name.
    if not made.startswith(b"%PDF"):
        raise SystemBusyError(say("docPrintFailed"))
    return Response(
        content=made,
        media_type="application/pdf",
        headers={
            **INERT_HEADERS,
            "Content-Disposition": 'attachment; filename="document.pdf"',
        },
    )


async def _print(endpoint: str, html: str) -> dict:
    """What the printing browser said about ``html``; a refusal if it said nothing."""
    try:
        async with httpx.AsyncClient(timeout=PRINT_TIMEOUT_S) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/print",
                json={"html": html, "margin": PDF_MARGIN},
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError(say("docPrintUnavailable")) from exc
    if response.status_code in _NOT_THERE:
        raise SystemBusyError(say("docPrintUnavailable"))
    if response.status_code != 200:
        raise SystemBusyError(say("docPrintFailed"))
    try:
        answer = response.json()
    except ValueError as exc:
        raise SystemBusyError(say("docPrintFailed")) from exc
    if not answer.get("ok"):
        raise SystemBusyError(say("docPrintFailed"))
    return answer
