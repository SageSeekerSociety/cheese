"""Where the expected binding comes from -- and never from admission.

FB-73's whole shape is a credential that was signed for the wrong seat. The
seat a session *is* and the seat its credential *acts as* are two different
declarations, and a rebuild after a disconnect issued one for the project
default while the card kept saying another. So the probe reads both, from
sources that do not reduce to a single admission answer:

* **the credential's own claim** -- a scoped token carries the handle it acts
  as in its ``a`` claim. The payload is base64url JSON; it is readable without
  the signing secret. Nothing here verifies the signature (that needs the
  platform's secret) and nothing here prints the token or its signature: the
  claim is a declaration, exactly the kind that can be wrong in the FB-73 way.
* **the session's own seat** -- the platform writes ``CHEESE_AUTHOR`` into the
  session environment (``machine_launcher.screen_env``). It is the handle the
  session's turns author under, and it does not come from the credential.
* **the seat's declared model** -- ``GET /topics/{topic}/agent/control`` answers
  with the session's ``state.init.model``: the model the session was launched
  with. A room credential may read it; ``GET /projects/{id}/agents`` may not
  (403 for a topic-scoped credential). This is the "card" side of the binding,
  and it is used here only as what *should* be running -- what actually ran is
  still decided by the wire (``report.build_provenance``).

Admission (``/llm/admission``) is deliberately NOT among these. It is the same
call the metering proxy makes to rewrite the request body and pick the pool, so
treating its answer as the expectation is a tautology -- ``expected == wire`` by
construction, and FB-73's false response reads as a match. It is kept only as a
third party to cross-check the independent expectation against.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The claim a scoped credential carries for the seat it acts as (FB-73: this is
#: what got signed for the wrong seat). ``sandbox_auth.mint_scoped_token``.
CLAIM_SEAT = "a"
#: A session-bound credential's payload prefix (``sandbox_auth.bind_resource_token``).
SCOPED_SESSION_PREFIX = "cxss_"

#: The environment variable the platform sets to the seat a session authors as.
SESSION_SEAT_ENV = "CHEESE_AUTHOR"


def credential_claims(token: str) -> dict | None:
    """The claims a scoped credential declares -- its payload, never its signature.

    The token is ``<base64url(json)>. <signature>``; only the first segment is
    decoded. A malformed token, or one whose payload is not a JSON object,
    returns ``None``. Nothing here verifies the signature or echoes any part of
    the token back; the caller gets the claims dict and nothing else.
    """
    if not token:
        return None
    body = token.split(".", 1)[0]
    if body.startswith(SCOPED_SESSION_PREFIX):
        body = body[len(SCOPED_SESSION_PREFIX) :]
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def credential_seat(token: str) -> str | None:
    """The seat handle a scoped credential acts as (claim ``a``), or ``None``.

    ``None`` covers a malformed/identity-less credential as well as one with a
    non-string claim: neither names a seat, and a token that names no seat is
    never a mismatch on its own (nothing to disagree with).
    """
    claims = credential_claims(token)
    seat = (claims or {}).get(CLAIM_SEAT)
    return seat if isinstance(seat, str) and seat else None


def session_seat(env: Mapping[str, str] | None = None) -> str | None:
    """The seat THIS session authors as, from the environment the platform set.

    ``CHEESE_AUTHOR`` is written by ``machine_launcher.screen_env`` and does not
    ride the credential, so it is a second, independent declaration of who the
    session is. Absent/empty returns ``None`` -- an unknown seat is not a
    mismatch.
    """
    source = os.environ if env is None else env
    seat = (source.get(SESSION_SEAT_ENV) or "").strip()
    return seat or None


@dataclass(frozen=True)
class SeatCheck:
    """The credential-vs-session seat comparison, and its verdict evidence."""

    ok: bool
    credential_seat: str | None
    session_seat: str | None
    reason: str


def check_seat(
    token: str,
    seat: str | None = None,
    env: Mapping[str, str] | None = None,
) -> SeatCheck:
    """Compare the credential's seat against the session's own seat.

    ``seat`` (the operator's ``--seat``) wins over the environment when given:
    when the platform sets no independent seat variable, the operator is the
    only independent source, and a mismatch means the credential was signed for
    a *different* seat than the one the operator names -- the FB-73 disease.

    A check that cannot see one of the two seats is ``ok`` with a reason saying
    so: an unreadable claim is not evidence of a wrong seat.
    """
    cred = credential_seat(token)
    session = (seat or "").strip() or session_seat(env)
    if cred is None:
        return SeatCheck(
            True,
            cred,
            session,
            "the credential names no seat; the seat check is inactive",
        )
    if not session:
        return SeatCheck(
            True,
            cred,
            None,
            "no independent seat to compare against (set --seat); the seat "
            "check is inactive",
        )
    if cred != session:
        return SeatCheck(
            False,
            cred,
            session,
            f"the credential was signed for another seat ({cred!r}); this "
            f"session is {session!r}",
        )
    return SeatCheck(True, cred, session, "the credential names this session's seat")


# --- the expected binding, from independent sources --------------------------


@dataclass
class DeclaredBinding:
    """What the seat says should be running, and where that came from.

    ``source`` is one of ``operator`` / ``seat-config`` / ``card`` / ``none`` so
    a reader can weigh the claim: an operator who names the model is the most
    trusted, the seat's own launch config next, and a card's turn record last.
    """

    model: str | None = None
    pool: str | None = None
    source: str = "none"

    def merged(self, other: DeclaredBinding) -> DeclaredBinding:
        """Prefer this instance's fields; fill the gaps from ``other``.

        Used to layer the operator's explicit choice over the seat's saved one:
        a model from ``--expected-model`` keeps its ``operator`` provenance even
        when the pool came from the seat config.
        """
        model = self.model or other.model
        pool = self.pool or other.pool
        source = self.source
        if other.model and other.model != self.model and source == "none":
            source = other.source
        elif self.source == "none" and other.source != "none":
            source = other.source
        return DeclaredBinding(model=model, pool=pool, source=source)


def declared_from_operator(
    expected_model: str | None, expected_pool: str | None
) -> DeclaredBinding:
    """The binding the operator named on the command line (highest trust)."""
    model = (expected_model or "").strip() or None
    pool = (expected_pool or "").strip() or None
    source = "operator" if (model or pool) else "none"
    return DeclaredBinding(model=model, pool=pool, source=source)


def declared_from_seat_config(payload: Mapping[str, Any] | None) -> DeclaredBinding:
    """The model a session's own control state declares it was launched with.

    ``payload`` is the ``data`` of ``GET /topics/{topic}/agent/control``; the
    model lives at ``data.state.init.model``. Only the model is available here --
    the pool is not on this payload -- so the pool is left ``None`` for the
    operator or the wire's own headers to answer.
    """
    state = (payload or {}).get("state")
    init = state.get("init") if isinstance(state, Mapping) else None
    model = init.get("model") if isinstance(init, Mapping) else None
    model = model.strip() if isinstance(model, str) and model.strip() else None
    return DeclaredBinding(
        model=model, pool=None, source="seat-config" if model else "none"
    )


def declared_from_card(blocks: list[Mapping[str, Any]] | None) -> DeclaredBinding:
    """The model a room's own turn record declares, if it carries one.

    The room's API surfaces a turn's *route* (``agent_turns.route``) but not its
    model on the block payload, so in practice this returns ``none`` and the
    reader leans on the seat config. Kept as the last, weakest source so a
    deployment that starts attaching the model to a block is picked up for free.
    """
    for block in reversed(blocks or []):
        meta = block.get("meta") if isinstance(block, Mapping) else None
        if not isinstance(meta, Mapping):
            continue
        model = meta.get("model")
        if isinstance(model, str) and model.strip():
            route = meta.get("route")
            pool = (
                route.strip() if isinstance(route, str) and route.strip() else None
            )
            return DeclaredBinding(
                model=model.strip(),
                pool=pool,
                source="card",
            )
    return DeclaredBinding()
