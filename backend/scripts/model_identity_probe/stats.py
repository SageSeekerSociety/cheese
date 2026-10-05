"""Statistics for the one-token fingerprint.

Ported from ``llm-fingerprint-detector`` ``src/stats.ts`` and ``src/constants.ts``
(MIT, Copyright (c) 2026 Tosea.ai and contributors). The distance between two
fingerprints is the mean of per-cell Jensen-Shannon divergences over the cells
where both sides hold enough valid samples -- arXiv:2607.10252.

Baselines from the paper, kept here for interpreting a result:
  same model, split half                       median JSD ~= 0.140
  same model, two providers                    median JSD ~= 0.227
  different models                             median JSD ~= 0.463
  equal error rate                             10.6% at 8 cells, 7.3% at 40

Thresholds are heuristics sitting between those medians, not proofs.

The equal-error-rate figure is a *paper* baseline (arXiv:2607.10252), quoted
here only so a reader can interpret a band; it is NOT reproduced by this
package (reproducing it needs the paper's labelled sample set, which the
platform's models are absent from). Do not cite it as a measured property of
this tool.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# --- thresholds (llm-fingerprint-detector constants.ts) -----------------------
JSD_MATCH_THRESHOLD = 0.25
JSD_MISMATCH_THRESHOLD = 0.35
JSD_BASELINE_SELF = 0.140
JSD_BASELINE_CROSS_PROVIDER = 0.227
JSD_BASELINE_DIFFERENT_MODEL = 0.463
MIN_VALID_SAMPLES_PER_CELL = 10
MIN_COMPARABLE_CELLS = 4
MIN_SPLIT_HALF_SAMPLES = 5
SPLIT_HALF_WARN_THRESHOLD = 0.25

# --- probe protocol (llm-fingerprint-detector constants.ts) ------------------
#: The paper's protocol sends temperature 1.0 *explicitly* -- some gateway
#: routes silently clamp a missing temperature to a different value, and a
#: sample drawn at a different temperature is a different distribution.
PROBE_TEMPERATURE = 1.0
#: Paper protocol truncates the answer to one token's worth of budget. Reasoning
#: must be off (see ``ReasoningAdapter``) or the hidden thinking consumes this
#: budget before any visible answer and every sample is empty.
PROBE_MAX_TOKENS = 16
#: Fallback when reasoning cannot be disabled: raise the budget so a visible
#: answer survives after the hidden reasoning, and flag lower confidence.
POST_REASONING_MAX_TOKENS = 1024
#: One cell count for every preset, the default and the references -- the paper
#: calibrates at 25 samples/cell, and a reference collected at a different count
#: is not comparable. ``MIN_VALID_SAMPLES_PER_CELL`` (10) is what actually gates
#: a cell; 25 gives a cell room to lose samples to refusals and still qualify.
DEFAULT_SAMPLES_PER_CELL = 25
DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2

FINGERPRINT_FORMAT_VERSION = 1
PROBE_PROTOCOL = "one-token/v1"


def shannon_entropy_bits(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for n in counts.values():
        if n <= 0:
            continue
        p = n / total
        entropy -= p * math.log2(p)
    return entropy


def jensen_shannon_divergence(
    counts_p: dict[str, int], counts_q: dict[str, int]
) -> float:
    """JSD base 2: ``H(M) - (H(P)+H(Q))/2`` with ``M = (P+Q)/2``, over the union
    of both supports. Range [0, 1] bit, not square-rooted."""
    total_p = sum(counts_p.values())
    total_q = sum(counts_q.values())
    if total_p <= 0 or total_q <= 0:
        return 0.0
    support = set(counts_p) | set(counts_q)
    h_m = h_p = h_q = 0.0
    for key in support:
        p = counts_p.get(key, 0) / total_p
        q = counts_q.get(key, 0) / total_q
        m = (p + q) / 2
        if m > 0:
            h_m -= m * math.log2(m)
        if p > 0:
            h_p -= p * math.log2(p)
        if q > 0:
            h_q -= q * math.log2(q)
    return min(1.0, max(0.0, h_m - (h_p + h_q) / 2))


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


@dataclass
class CellSamples:
    """Every answer collected for one cell, normalised.

    ``order`` keeps the valid answers in the order they *arrived*, which is what
    the split-half self-check needs (see ``split_half_jsd``). It is deliberately
    not serialised: a reference is compared through its counts, and arrival
    order is meaningless for a fingerprint read back off disk.
    """

    counts: Counter[str] = field(default_factory=Counter)
    valid: int = 0
    invalid: int = 0
    refusal: int = 0
    empty: int = 0
    error: int = 0
    input_tokens: list[int] = field(default_factory=list)
    output_tokens: list[int] = field(default_factory=list)
    order: list[str] = field(default_factory=list)

    def add(
        self, category: str, normalized: str | None, usage: dict | None = None
    ) -> None:
        if usage:
            if isinstance(usage.get("input_tokens"), int):
                self.input_tokens.append(usage["input_tokens"])
            if isinstance(usage.get("output_tokens"), int):
                self.output_tokens.append(usage["output_tokens"])
        if category == "valid":
            self.valid += 1
            if normalized is not None:
                self.counts[normalized] += 1
                self.order.append(normalized)
        elif category == "invalid":
            self.invalid += 1
        elif category == "refusal":
            self.refusal += 1
        elif category == "empty":
            self.empty += 1
        else:
            self.error += 1

    @property
    def total(self) -> int:
        return self.valid + self.invalid + self.refusal + self.empty + self.error

    def to_json(self) -> dict:
        return {
            "counts": dict(self.counts),
            "valid": self.valid,
            "invalid": self.invalid,
            "refusal": self.refusal,
            "empty": self.empty,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: dict) -> CellSamples:
        out = cls()
        out.counts = Counter(data.get("counts") or {})
        for key in ("valid", "invalid", "refusal", "empty", "error"):
            setattr(out, key, int(data.get(key, 0)))
        return out


@dataclass(frozen=True, slots=True)
class CellJsd:
    cell_id: str
    jsd: float
    valid_a: int
    valid_b: int


def compare_cells(
    a: dict[str, CellSamples],
    b: dict[str, CellSamples],
    min_valid: int = MIN_VALID_SAMPLES_PER_CELL,
) -> list[CellJsd]:
    """Per-cell JSD over the cells both sides filled with enough valid answers.

    Sorted by descending JSD, like the upstream ``compareCellSets``: the worst
    cells are the ones a reader looks at first."""
    entries: list[CellJsd] = []
    for cell_id in a:
        other = b.get(cell_id)
        if other is None:
            continue
        if a[cell_id].valid < min_valid or other.valid < min_valid:
            continue
        entries.append(
            CellJsd(
                cell_id=cell_id,
                jsd=jensen_shannon_divergence(
                    dict(a[cell_id].counts), dict(other.counts)
                ),
                valid_a=a[cell_id].valid,
                valid_b=other.valid,
            )
        )
    entries.sort(key=lambda entry: entry.jsd, reverse=True)
    return entries


def mean_jsd(entries: list[CellJsd]) -> float | None:
    if not entries:
        return None
    return sum(e.jsd for e in entries) / len(entries)


def split_half_jsd(
    answers: list[str], min_per_half: int = MIN_SPLIT_HALF_SAMPLES
) -> float | None:
    """JSD between one cell's two arrival-parity halves.

    This is the upstream ``splitHalfJsd``: the valid answers are laid out in the
    order they arrived and split by ``index % 2`` -- even positions against odd
    positions. A *stable* cell answers with the same handful of values in a
    randomised order, so the two halves hold the same distribution and JSD is
    near zero. An endpoint that is really two backends behind one name (an
    aggregator, or a route that alternates) tends to alternate its answers
    ``a, b, a, b, ...``; the even half is then almost all ``a`` and the odd half
    almost all ``b``, so JSD approaches 1.0 -- the instability the *mean* JSD
    against a reference cannot see.

    The split must be by arrival order, NOT a shuffle: shuffling a bag of counts
    destroys exactly the signal this check exists to catch (it puts every ``a``
    and every ``b`` in both halves and reports ~0 for the alternating case the
    upstream tool reports as ~1.0)."""
    even: Counter[str] = Counter()
    odd: Counter[str] = Counter()
    even_n = odd_n = 0
    for index, answer in enumerate(answers):
        if index % 2 == 0:
            even[answer] += 1
            even_n += 1
        else:
            odd[answer] += 1
            odd_n += 1
    if even_n < min_per_half or odd_n < min_per_half:
        return None
    return jensen_shannon_divergence(dict(even), dict(odd))


def split_half_mean(cells: dict[str, CellSamples]) -> float | None:
    """The mean of the per-cell split-half JSDs over the cells with enough
    samples in each half -- the single number the upstream check returns (it
    averages across cells; the earlier port took the max). ``None`` when no cell
    qualified."""
    values = [
        value
        for value in (split_half_jsd(samples.order) for samples in cells.values())
        if value is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)
