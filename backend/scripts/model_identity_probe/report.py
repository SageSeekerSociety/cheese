"""One verification run, end to end: three signals, one verdict.

The shape of the answer is fixed: for each endpoint under test, say
``match`` / ``mismatch`` / ``uncertain`` / ``insufficient``, show the evidence
each signal produced, and state how many samples backed it. No verdict is ever
drawn from what the model says about itself -- the identity questions the
LLMmap probe set asks ("what model are you?") are deliberately absent, because
a model's self-report is the one thing an impostor controls for free.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import battery
from .collect import collect
from .reference import Reference
from .stats import (
    DEFAULT_CONCURRENCY,
    DEFAULT_SAMPLES_PER_CELL,
    PROBE_PROTOCOL,
    SPLIT_HALF_WARN_THRESHOLD,
    CellSamples,
    compare_cells,
    mean_jsd,
    split_half_jsd,
)
from .tokenizer_fp import compare as compare_tokenizer
from .tokenizer_fp import measure
from .transport import Endpoint, pool_from_headers
from .verdict import (
    MATCH,
    MISMATCH,
    UNCERTAIN,
    BehaviourEvidence,
    ProvenanceEvidence,
    combine,
    decide_jsd_verdict,
)


@dataclass
class Verification:
    model: str
    target: str
    verdict: str
    reason: str
    provenance: ProvenanceEvidence | None
    behaviour: BehaviourEvidence | None
    tokenizer: object | None
    behaviour_samples: int
    tokenizer_samples: int
    protocol: str = PROBE_PROTOCOL

    def to_json(self) -> dict:
        return {
            "model": self.model,
            "target": self.target,
            "verdict": self.verdict,
            "reason": self.reason,
            "protocol": self.protocol,
            "samples": {
                "behaviour_requests": self.behaviour_samples,
                "tokenizer_requests": self.tokenizer_samples,
            },
            "signals": {
                "provenance": self.provenance.to_json() if self.provenance else None,
                "behaviour": self.behaviour.to_json() if self.behaviour else None,
                "tokenizer": self.tokenizer.to_json() if self.tokenizer else None,
            },
        }


def build_provenance(claimed: str, completion) -> ProvenanceEvidence:
    """What the wire said about itself for one request."""
    headers = completion.headers or {}
    upstream = headers.get("x-litellm-model-name")
    pool = pool_from_headers(headers)
    echo = completion.model_echo
    echo_matches = None if echo is None else (echo == claimed)
    if upstream:
        upstream_base = upstream.rsplit("/", 1)[-1]
        verdict = MATCH if upstream_base == claimed else MISMATCH
    elif echo_matches is False:
        # The model named itself something else. Worth reporting, but a name in
        # a body is the model's own word -- uncertainty, not a mismatch.
        verdict = UNCERTAIN
    elif echo_matches is True:
        verdict = MATCH
    else:
        verdict = UNCERTAIN
    return ProvenanceEvidence(
        claimed_model=claimed,
        wire_model=completion.model_echo,
        upstream_model_name=upstream,
        pool=pool,
        echo_matches_claim=echo_matches,
        verdict=verdict,
    )


def build_behaviour(
    target_cells: dict[str, CellSamples], reference: Reference | None
) -> BehaviourEvidence:
    if reference is None:
        return BehaviourEvidence(None, 0, "insufficient")
    entries = compare_cells(reference.cells, target_cells)
    mean = mean_jsd(entries)
    split = [
        value
        for value in (split_half_jsd(s) for s in target_cells.values())
        if value is not None
    ]
    worst = max(split) if split else None
    return BehaviourEvidence(
        mean_jsd=mean,
        comparable_cells=len(entries),
        verdict=decide_jsd_verdict(mean, len(entries)),
        cells=entries,
        split_half_max=worst,
        unstable_routing=bool(worst is not None and worst > SPLIT_HALF_WARN_THRESHOLD),
    )


def verify(
    endpoint: Endpoint,
    claimed_model: str,
    reference: Reference | None,
    *,
    sample_tokenizer: bool = True,
    samples_per_cell: int = DEFAULT_SAMPLES_PER_CELL,
    concurrency: int = DEFAULT_CONCURRENCY,
    seed: int = 20261005,
    progress=None,
) -> Verification:
    cells = (
        tuple(reference.cells)
        if reference and reference.cells
        else battery.cells_for_preset("quick")
    )

    provenance = None
    behaviour = None
    tokenizer = None
    behaviour_samples = 0
    tokenizer_samples = 0

    if reference is not None and reference.cells:
        collected = collect(
            endpoint, cells, samples_per_cell, concurrency, seed, progress
        )
        behaviour_samples = sum(s.total for s in collected.values())
        behaviour = build_behaviour(collected, reference)
    if sample_tokenizer:
        sample = measure(endpoint)
        tokenizer_samples = 1 + len(sample.deltas)
        tokenizer = compare_tokenizer(
            sample, reference.tokenizer if reference else None
        )

    # One extra request carries the headers and the echo for the provenance row.
    if reference is not None and reference.cells:
        first_cell = next(iter(cells))
        completion = endpoint.complete(
            battery.system_prompt(first_cell),
            battery.pick_paraphrase(first_cell, random.Random(seed)),
        )
        provenance = build_provenance(claimed_model, completion)

    verdict, reason = combine(provenance, behaviour, tokenizer)
    target = f"{endpoint.mode}:{endpoint.model}"
    return Verification(
        claimed_model,
        target,
        verdict,
        reason,
        provenance,
        behaviour,
        tokenizer,
        behaviour_samples,
        tokenizer_samples,
    )


def verify_all(
    endpoints: list[Endpoint],
    references: dict[str, Reference],
    **kwargs,
) -> list[dict]:
    return [
        verify(
            endpoint, endpoint.model, references.get(endpoint.model), **kwargs
        ).to_json()
        for endpoint in endpoints
    ]
