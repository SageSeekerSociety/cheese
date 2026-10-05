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
"""

from __future__ import annotations

import math
import random
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

# --- probe protocol ----------------------------------------------------------
PROBE_TEMPERATURE = 1.0
PROBE_MAX_TOKENS = 256
DEFAULT_SAMPLES_PER_CELL = 12
DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_MAX_RETRIES = 3

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
    """Every answer collected for one cell, normalised."""

    counts: Counter[str] = field(default_factory=Counter)
    valid: int = 0
    invalid: int = 0
    refusal: int = 0
    empty: int = 0
    error: int = 0
    input_tokens: list[int] = field(default_factory=list)
    output_tokens: list[int] = field(default_factory=list)

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
    """Per-cell JSD over the cells both sides filled with enough valid answers."""
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
    return entries


def mean_jsd(entries: list[CellJsd]) -> float | None:
    if not entries:
        return None
    return sum(e.jsd for e in entries) / len(entries)


def split_half_jsd(samples: CellSamples, seed: int = 1) -> float | None:
    """JSD of a cell's own two halves. A high value means the endpoint is not
    one distribution -- a multi-backend aggregator, or routing that varies.

    The samples are shuffled before the split: the cell here is a bag of
    counts, and splitting a bag by insertion order would put every ``a`` in one
    half and every ``b`` in the other, reporting 1.0 for a perfectly stable
    cell. The reference implementation splits the raw sample list, whose order
    is already the (effectively random) order the answers arrived in."""
    items: list[str] = []
    for value, count in samples.counts.items():
        items.extend([value] * count)
    if len(items) < 2 * MIN_SPLIT_HALF_SAMPLES:
        return None
    random.Random(seed).shuffle(items)
    half = len(items) // 2
    left = Counter(items[:half])
    right = Counter(items[half:])
    if (
        sum(left.values()) < MIN_SPLIT_HALF_SAMPLES
        or sum(right.values()) < MIN_SPLIT_HALF_SAMPLES
    ):
        return None
    return jensen_shannon_divergence(dict(left), dict(right))
