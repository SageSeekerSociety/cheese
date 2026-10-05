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
from .binding import SeatCheck
from .collect import collect
from .model_names import alias_ambiguous, normalize_model_name
from .reference import Reference
from .stats import (
    DEFAULT_CONCURRENCY,
    DEFAULT_SAMPLES_PER_CELL,
    PROBE_PROTOCOL,
    PROBE_TEMPERATURE,
    SPLIT_HALF_WARN_THRESHOLD,
    CellSamples,
    compare_cells,
    mean_jsd,
    split_half_mean,
)
from .tokenizer_fp import compare as compare_tokenizer
from .tokenizer_fp import measure
from .transport import Endpoint, detect_adapter, pool_from_headers
from .verdict import (
    MATCH,
    MISMATCH,
    UNCERTAIN,
    AdmissionReading,
    BehaviourEvidence,
    ProvenanceEvidence,
    combine,
    decide_jsd_verdict,
)

#: The name-folding rule lives in ``model_names`` so the wire comparison here
#: and the admission cross-check in ``verdict`` fold names identically.
_alias_ambiguous = alias_ambiguous


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
    expected_pool: str | None = None
    expected_model: str | None = None
    seat: SeatCheck | None = None
    #: Set when the one wire probe could not be made at all (a refused CONNECT,
    #: an unreachable listener): the wire evidence is then absent, not agreeing,
    #: and a reader must not mistake an unreachable endpoint for an inconclusive
    #: one.
    wire_error: str | None = None
    protocol: str = PROBE_PROTOCOL

    def to_json(self) -> dict:
        return {
            "model": self.model,
            "target": self.target,
            "verdict": self.verdict,
            "reason": self.reason,
            "protocol": self.protocol,
            "wire_error": self.wire_error,
            "binding": {
                "expected_pool": self.expected_pool,
                "expected_model": self.expected_model,
            },
            "seat": (
                None
                if self.seat is None
                else {
                    "ok": self.seat.ok,
                    "credential_seat": self.seat.credential_seat,
                    "session_seat": self.seat.session_seat,
                    "reason": self.seat.reason,
                }
            ),
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


def build_provenance(
    claimed: str,
    completion,
    expected_pool: str | None = None,
) -> ProvenanceEvidence:
    """What the wire said about itself for one request.

    A name that does not fold onto the claim is a mismatch -- the provider's
    echo and the upstream name in the headers are wire facts, not the model's
    self-report. Two exceptions keep it honest: a dated snapshot or provider
    prefix folds away (``claude-opus-5-5-20250915`` is the claim), and two
    names where one is a prefix of the other are an *alias a human has to
    settle* -- uncertain, never a mismatch on a naming accident.
    """
    headers = completion.headers or {}
    upstream = headers.get("x-litellm-model-name")
    pool = pool_from_headers(headers)
    echo = completion.model_echo
    claimed_norm = normalize_model_name(claimed)

    # Both branches compare with the same normalized rule: an exact string match
    # and a normalized one are the same judgement (``claude-opus-5-5`` and
    # ``anthropic/claude-opus-5-5-20250915`` are one model), and a header that
    # was compared strictly while the echo was compared loosely let the two
    # disagree on paper without any check noticing.
    echo_matches = (
        None if echo is None else normalize_model_name(echo) == claimed_norm
    )
    if upstream:
        upstream_norm = normalize_model_name(upstream)
        if upstream_norm == claimed_norm:
            # The header named the claim, but the body echoed something else:
            # the response is internally inconsistent about its own model, and
            # the header agreeing must not paper over the body's disagreement.
            # This is the FB-73 shape seen through a single response.
            if echo is not None and not echo_matches:
                verdict = MISMATCH
            else:
                verdict = MATCH
        elif _alias_ambiguous(claimed_norm, upstream_norm):
            verdict = UNCERTAIN
        else:
            verdict = MISMATCH
    elif echo is not None:
        if echo_matches:
            verdict = MATCH
        elif _alias_ambiguous(claimed_norm, normalize_model_name(echo)):
            verdict = UNCERTAIN
        else:
            # The body's model is the provider's echo of what it served, not a
            # self-report: a disagreement here is a deterministic fact, so it is
            # a mismatch, not a hedge.
            verdict = MISMATCH
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
    split = split_half_mean(target_cells)
    return BehaviourEvidence(
        mean_jsd=mean,
        comparable_cells=len(entries),
        verdict=decide_jsd_verdict(mean, len(entries)),
        cells=entries,
        split_half_mean=split,
        unstable_routing=bool(split is not None and split > SPLIT_HALF_WARN_THRESHOLD),
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
    expected_pool: str | None = None,
    expected_model: str | None = None,
    admission: AdmissionReading | None = None,
    seat: SeatCheck | None = None,
    progress=None,
) -> Verification:
    # The expectation the wire is judged against: the independently-declared
    # model (operator / seat config) when there is one, else the one under test.
    # Admission never sets this (R3-1) -- it is only cross-checked below.
    expected_model = expected_model or claimed_model
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
    adapter = None

    if reference is not None and reference.cells:
        # The reasoning-disable field is a property of the endpoint, discovered
        # once; the same adapter must apply to the reference (recorded in its
        # notes) or the two fingerprints were drawn under different conditions.
        adapter = detect_adapter(endpoint, next(iter(cells)))
        collected = collect(
            endpoint,
            cells,
            samples_per_cell,
            concurrency,
            seed,
            progress,
            adapter=adapter,
        )
        behaviour_samples = sum(s.total for s in collected.values())
        behaviour = build_behaviour(collected, reference)
    if sample_tokenizer:
        if adapter is None:
            adapter = detect_adapter(endpoint, next(iter(cells)))
        sample = measure(endpoint, adapter=adapter)
        tokenizer_samples = 1 + len(sample.deltas)
        tokenizer = compare_tokenizer(
            sample, reference.tokenizer if reference else None
        )

    # One request always carries the headers, the echo and the usage for the
    # provenance row -- with or without a reference. Without it (a subscription
    # seat, which has none) the deterministic signals are the *only* evidence,
    # and skipping the request is what left FB-73-adjacent checks "insufficient".
    first_cell = next(iter(cells))
    completion = endpoint.complete(
        battery.system_prompt(first_cell),
        battery.pick_paraphrase(first_cell, random.Random(seed)),
        temperature=adapter.temperature if adapter is not None else PROBE_TEMPERATURE,
        extra_body=adapter.extra_body if adapter else None,
    )
    # The wire is judged against the EXPECTED model -- the independent claim --
    # not merely the id under test: an --expected-model the wire contradicts is a
    # mismatch on the wire's own terms (R3-4).
    provenance = build_provenance(expected_model, completion, expected_pool)

    verdict, reason = combine(
        provenance,
        behaviour,
        tokenizer,
        expected_pool=expected_pool,
        expected_model=expected_model,
        admission=admission,
        seat=seat,
    )
    target = f"{endpoint.mode}:{endpoint.model}"
    return Verification(
        expected_model,
        target,
        verdict,
        reason,
        provenance,
        behaviour,
        tokenizer,
        behaviour_samples,
        tokenizer_samples,
        expected_pool=expected_pool,
        expected_model=expected_model,
        seat=seat,
        wire_error=completion.error,
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
