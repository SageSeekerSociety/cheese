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

from dataclasses import dataclass, field

from .stats import (
    JSD_MATCH_THRESHOLD,
    JSD_MISMATCH_THRESHOLD,
    MIN_COMPARABLE_CELLS,
    CellJsd,
)

MATCH = "match"
MISMATCH = "mismatch"
UNCERTAIN = "uncertain"
INSUFFICIENT = "insufficient"

_RANK = {MATCH: 0, INSUFFICIENT: 1, UNCERTAIN: 2, MISMATCH: 3}


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
    split_half_max: float | None = None
    unstable_routing: bool = False

    def to_json(self) -> dict:
        return {
            "mean_jsd": None if self.mean_jsd is None else round(self.mean_jsd, 4),
            "comparable_cells": self.comparable_cells,
            "verdict": self.verdict,
            "split_half_max": (
                None if self.split_half_max is None else round(self.split_half_max, 4)
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


def combine(
    provenance: ProvenanceEvidence | None,
    behaviour: BehaviourEvidence | None,
    tokenizer: TokenizerEvidence | None,
) -> tuple[str, str]:
    """Return (verdict, reason). Deterministic disagreements win outright."""
    if provenance is not None and provenance.verdict == MISMATCH:
        return MISMATCH, "the upstream named a different model than the one claimed"
    if behaviour is not None and behaviour.verdict == MISMATCH:
        return MISMATCH, "the answer distribution differs from the reference"
    if (
        behaviour is not None
        and behaviour.verdict == MATCH
        and tokenizer is not None
        and tokenizer.verdict == MISMATCH
    ):
        return UNCERTAIN, (
            "the answer distribution matches but the tokenizer fingerprint does not; "
            "routing may be mixed"
        )
    if behaviour is not None and behaviour.verdict == MATCH:
        if tokenizer is None or tokenizer.verdict in (MATCH, INSUFFICIENT):
            return MATCH, "the answer distribution matches the reference"
    if behaviour is not None and behaviour.verdict == UNCERTAIN:
        return UNCERTAIN, "the answer distribution sits in the uncertainty band"
    if behaviour is None:
        return INSUFFICIENT, "no behavioural comparison was made"
    return INSUFFICIENT, "too few comparable cells to decide"
