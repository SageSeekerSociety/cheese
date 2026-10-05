"""Turning evidence into one verdict per target.

Three independent signals are combined, weakest-to-strongest in what they can
prove:

  provenance  what the wire said about itself (upstream model name in the
              response, which pool's headers came back). Deterministic and
              free, but a middlebox can lie about it.
  tokenizer   the server's own ``usage.input_tokens`` for a fixed probe minus
              a fixed base. It is the billing number, so it is expensive to
              fake -- but it only proves a shared tokenizer, not shared
              weights.
  behaviour   the one-token answer distribution against a reference built on a
              trusted endpoint (arXiv:2607.10252). The only signal that speaks
              to the weights themselves.

The rule below is deliberately conservative: a *deterministic* disagreement
(the claimed wire model is not the one the upstream named) is a mismatch on
its own, because there is no statistical story that explains it. A statistical
mismatch is a mismatch only when the behavioural signal also disagrees; the
tokenizer alone can differ across two deployments of the same model family.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .binding import SeatCheck
from .model_names import alias_ambiguous, normalize_model_name
from .stats import (
    JSD_MATCH_THRESHOLD,
    JSD_MISMATCH_THRESHOLD,
    MIN_COMPARABLE_CELLS,
    SPLIT_HALF_WARN_THRESHOLD,
    CellJsd,
)

MATCH = "match"
MISMATCH = "mismatch"
UNCERTAIN = "uncertain"
INSUFFICIENT = "insufficient"

_RANK = {MATCH: 0, INSUFFICIENT: 1, UNCERTAIN: 2, MISMATCH: 3}


def _admission_conflict(
    admission: AdmissionReading | None,
    expected_model: str | None,
    expected_pool: str | None,
) -> str | None:
    """A mismatch reason when admission resolved elsewhere than expected, else None.

    Admission drives the metering proxy's body rewrite and pool choice, so if it
    answers with a different model or pool than the seat is declared to be, the
    turn will run as the admission's answer -- the FB-73 disease, whatever the
    credential was signed for. An unknown on either side is not a conflict; only
    a positive disagreement is.
    """
    if admission is None:
        return None
    if expected_model and admission.model:
        want = normalize_model_name(expected_model)
        got = normalize_model_name(admission.model)
        if want != got and not alias_ambiguous(want, got):
            return (
                f"the seat is declared as {expected_model!r} but admission "
                f"resolved it to {admission.model!r} (the credential or its "
                "resolution is wrong, and the turn will run as the admission's)"
            )
    if expected_pool and admission.pool and admission.pool != expected_pool:
        return (
            f"the seat is declared on the {expected_pool} pool but admission "
            f"resolved it to the {admission.pool} pool"
        )
    return None


def decide_jsd_verdict(mean: float | None, comparable_cells: int) -> str:
    """Ported from ``llm-fingerprint-detector`` ``src/verdict.ts``."""
    if mean is None or comparable_cells < MIN_COMPARABLE_CELLS:
        return INSUFFICIENT
    if mean <= JSD_MATCH_THRESHOLD:
        return MATCH
    if mean <= JSD_MISMATCH_THRESHOLD:
        return UNCERTAIN
    return MISMATCH


@dataclass
class BehaviourEvidence:
    mean_jsd: float | None
    comparable_cells: int
    verdict: str
    cells: list[CellJsd] = field(default_factory=list)
    split_half_mean: float | None = None
    unstable_routing: bool = False

    def to_json(self) -> dict:
        return {
            "mean_jsd": None if self.mean_jsd is None else round(self.mean_jsd, 4),
            "comparable_cells": self.comparable_cells,
            "verdict": self.verdict,
            "split_half_mean": (
                None if self.split_half_mean is None else round(self.split_half_mean, 4)
            ),
            "unstable_routing": self.unstable_routing,
            "cells": [
                {
                    "cell": c.cell_id,
                    "jsd": round(c.jsd, 4),
                    "valid_reference": c.valid_a,
                    "valid_target": c.valid_b,
                }
                for c in self.cells
            ],
        }


@dataclass
class TokenizerEvidence:
    verdict: str
    deltas: dict[str, int | None] = field(default_factory=dict)
    reference_deltas: dict[str, int] = field(default_factory=dict)
    base_input_tokens: int | None = None
    reference_base: int | None = None

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "base_input_tokens": self.base_input_tokens,
            "reference_base": self.reference_base,
            "deltas": {k: v for k, v in self.deltas.items()},
            "reference_deltas": {k: v for k, v in self.reference_deltas.items()},
        }


@dataclass
class ProvenanceEvidence:
    claimed_model: str
    wire_model: str | None
    upstream_model_name: str | None
    pool: str | None
    echo_matches_claim: bool | None
    verdict: str

    def to_json(self) -> dict:
        return {
            "claimed_model": self.claimed_model,
            "wire_model": self.wire_model,
            "upstream_model_name": self.upstream_model_name,
            "pool": self.pool,
            "echo_matches_claim": self.echo_matches_claim,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class AdmissionReading:
    """What ``/llm/admission`` answered, kept as a third party -- not the truth.

    Admission is the same call the metering proxy makes to rewrite the request
    body and pick the pool, so an expectation taken from it is a tautology:
    ``expected == wire`` by construction and FB-73's false answer reads as a
    match (R3-1). It is compared *against* the independent expectation instead,
    and a disagreement is a mismatch -- the credential resolved to one thing
    while the seat is declared to be another.
    """

    model: str | None = None
    pool: str | None = None
    allow: bool | None = None

    @classmethod
    def from_mapping(cls, binding: Mapping[str, Any] | None) -> AdmissionReading | None:
        if not isinstance(binding, Mapping):
            return None
        model = binding.get("model")
        pool = binding.get("pool")
        return cls(
            model=model if isinstance(model, str) and model else None,
            pool=pool if isinstance(pool, str) and pool else None,
            allow=binding.get("allow"),
        )


def combine(
    provenance: ProvenanceEvidence | None,
    behaviour: BehaviourEvidence | None,
    tokenizer: TokenizerEvidence | None,
    *,
    expected_pool: str | None = None,
    expected_model: str | None = None,
    admission: AdmissionReading | None = None,
    seat: SeatCheck | None = None,
) -> tuple[str, str]:
    """Return (verdict, reason).

    Precedence, weakest-to-strongest evidence, and the one rule each corner
    enforces:

      * a deterministic disagreement (the wire named a different model, or the
        request came back from the *other* pool than the seat is bound to) is a
        mismatch on its own -- no statistical story explains it;
      * an unstable route (split-half above ``SPLIT_HALF_WARN_THRESHOLD``)
        cannot be a clean match even when its mean JSD looks fine: one name
        answering from two distributions is the aggregator case, so it is
        downgraded to uncertain;
      * otherwise the behavioural and tokenizer signals decide, as before.

    ``expected_pool`` / ``expected_model`` are what the *independent* source
    (the operator, or the seat's own saved config) declares the seat should be.
    They are the expectation; ``admission`` is only a third party cross-checked
    against them. A response whose headers say a different pool than the seat is
    bound to is the FB-73 shape caught without any reference fingerprint.

    Two deterministic disagreements are FB-73's own, and each is a mismatch on
    its own -- either one alone catches the case, which is what keeps the probe
    honest when the other source is unavailable:

      * the credential was signed for a different seat than this session is
        (``seat``); and
      * admission resolved the request to a different model or pool than the
        seat is declared to be (``admission`` vs ``expected_*``).
    """
    if seat is not None and not seat.ok:
        return MISMATCH, seat.reason
    if provenance is not None and provenance.verdict == MISMATCH:
        return MISMATCH, "the upstream named a different model than the one claimed"
    if (
        provenance is not None
        and expected_pool
        and provenance.pool
        and provenance.pool != expected_pool
    ):
        return MISMATCH, (
            f"the seat is bound to the {expected_pool} pool but the response "
            f"came back from the {provenance.pool} pool"
        )
    conflict = _admission_conflict(admission, expected_model, expected_pool)
    if conflict is not None:
        return MISMATCH, conflict
    if behaviour is None:
        # No reference: the deterministic signals still decide. The wire model
        # and pool agreeing is weaker than a behavioural match, but it is a
        # real fact the card cannot see, and it is what a subscription seat
        # (which has no reference here) can be checked against at all.
        if provenance is None:
            return INSUFFICIENT, "no signal was available"
        if provenance.verdict == MATCH:
            return MATCH, (
                "the wire named the claimed model and the pool matches "
                "(no behavioural reference was available)"
            )
        return UNCERTAIN, (
            "no behavioural reference; the wire signals neither confirm nor "
            "contradict the claim"
        )
    if behaviour.verdict == MISMATCH:
        return MISMATCH, "the answer distribution differs from the reference"
    if behaviour.verdict == MATCH and behaviour.unstable_routing:
        # A hand-built evidence may set the flag without the statistic; the
        # threshold is on the statistic, so format it defensively rather than
        # raise on ``None``.
        self_check = (
            "no split-half statistic was computed"
            if behaviour.split_half_mean is None
            else (
                f"{behaviour.split_half_mean:.4f} > {SPLIT_HALF_WARN_THRESHOLD}"
            )
        )
        return UNCERTAIN, (
            "the answer distribution matches on average, but the split-half "
            "self-check is above the instability threshold "
            f"({self_check}); the endpoint may be rotating between backends"
        )
    if (
        behaviour.verdict == MATCH
        and tokenizer is not None
        and tokenizer.verdict == MISMATCH
    ):
        return UNCERTAIN, (
            "the answer distribution matches but the tokenizer fingerprint does not; "
            "routing may be mixed"
        )
    if behaviour.verdict == MATCH:
        if tokenizer is None or tokenizer.verdict in (MATCH, INSUFFICIENT):
            return MATCH, "the answer distribution matches the reference"
    if behaviour.verdict == UNCERTAIN:
        return UNCERTAIN, "the answer distribution sits in the uncertainty band"
    return INSUFFICIENT, "too few comparable cells to decide"
