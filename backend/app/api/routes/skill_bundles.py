"""The skill set a session installs, addressed by content (sha256).

A Claude Code launch used to carry this project's skills inline, base64+gzip
inside the launch script. The platform set alone is ~250KB compressed and rode
in every script for every session, so a device that already had the bytes paid
to be sent them again (device_launch). Now the script carries only the digest
and fetches the bundle here the first time a machine meets it — the machine
caches it under ``$HOME/.cheese/skill-bundles/<digest>.json.gz`` and asks again
never for a bundle it already holds.

Addressed by digest, not by project: the caller names only what it wants, and
the ONE project it may ask about is the one its own credential is scoped to.
The token's claim is authoritative — the path carries no project id, so there
is nothing for a caller to spoof. What is served is the same
``session_skill_bundle`` the launcher digest was taken from, so a digest the
caller holds either matches byte for byte or 404s; a project cannot be handed
another project's skills.
"""

import hashlib
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.project_skill.service import (
    session_skill_bundle,
    stored_session_skill_bundle,
)

router = APIRouter(prefix="/connector", tags=["connector"])

#: A sha256 in lowercase hex: exactly what ``session_skill_bundle``'s digest is,
#: and what the script embeds. Anything else is a malformed request, not a 404.
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


@router.get("/skill-bundles/{digest}")
async def download_skill_bundle(digest: str, request: Request) -> Response:
    """This credential's project skill bundle, if it hashes to ``digest``.

    The route is scoped-token only: a project claim names the one project whose
    skills may go out, and computing the bundle for exactly that project is what
    makes a digest mismatch a 404 rather than a leak. The global sandbox secret
    (dev / trusted-single-host) carries no project and is refused — a session
    always launches with a scoped credential, so it never needs it here.
    """
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    project_id = claims.get("p") if claims else None
    if not project_id:
        raise AuthenticationRequiredError("A project-scoped credential is required")
    if not _DIGEST_RE.fullmatch(digest):
        raise BadRequestError("digest must be a lowercase sha256 in hex")
    # The copy frozen when the launcher was built first; the skills may have
    # changed since. Recomputing is the fallback for a launch built elsewhere.
    bundle = stored_session_skill_bundle(project_id, digest)
    if bundle is None:
        bundle = session_skill_bundle(project_id)
    if hashlib.sha256(bundle).hexdigest() != digest:
        raise NotFoundError("no skill bundle with that digest for this project")
    return Response(
        bundle,
        media_type="application/gzip",
        headers={
            "X-Checksum-SHA256": digest,
            # The digest IS the content address, so these bytes never change for
            # a digest. Private: they are one project's, behind its credential,
            # and a shared cache must not replay them to anyone holding a digest.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )
