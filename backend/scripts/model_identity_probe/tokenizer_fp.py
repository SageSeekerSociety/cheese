"""Tokenizer fingerprint: the server's own token count, as a hard signal.

Method from "The Tokenizer Is a Fingerprint"
(https://isimplifyme.com/whitepapers/the-tokenizer-is-a-fingerprint), reached
through the side-channel survey in this repo's task notes. The idea is small
and it is the cheapest thing here that is not a claim by the model:

    delta(probe) = usage.input_tokens(BASE + probe) - usage.input_tokens(BASE)

Each vendor ships its own tokenizer, so the same probe string costs a
different number of tokens per model. Subtracting a fixed BASE cancels the
constant per-model template overhead, leaving a value that depends on the
probe's bytes under that model's vocabulary.

What it does NOT prove: two deployments of the same lab's models may share a
vocabulary (which is why several OpenAI-family rows agree), and a model that
merely borrows another's tokenizer would pass. It proves a shared request
pipeline, not shared weights -- so it is evidence beside the behavioural test,
never a replacement for it.

A caution earned the hard way on this box: use the ``usage`` of a real
``/v1/messages`` call. A ``count_tokens`` endpoint is not the upstream
tokenizer -- behind LiteLLM it is hard-coded to Anthropic's or falls back to a
local ``tiktoken``, and would fingerprint the gateway, not the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .reference import TokenizerReference
from .transport import Endpoint
from .verdict import INSUFFICIENT, MATCH, MISMATCH, TokenizerEvidence

#: A fixed, short BASE the count is measured against. Kept ASCII and boring so
#: it does not itself vary between vendors more than the probe does.
BASE = "ok"

#: Probe strings chosen to land differently in different vocabularies: scripts
#: with their own code points, emoji, whitespace-sensitive code, and plain
#: Latin as a control.
PROBES: dict[str, str] = {
    "latin": "the quick brown fox jumps over the lazy dog",
    "chinese": "请把这一段中文翻译成英文并保持原意",
    "japanese": "これはトークナイザの違いを見るためのテストです",
    "korean": "이 문장은 토크나이저 차이를 확인하기 위한 것입니다",
    "russian": "Это предложение для проверки различий токенизаторов",
    "emoji": "🌏🧪🔬🎯🚀📚🍣🎈",
    "code": "def f(x):\n    return {'a': [1, 2, 3]}\n",
    "unicode-mix": "café • naïve — ½ ⅓ ± Δ ∑",
    "digits": "0123456789012345678901234567890123456789",
    "whitespace": "a        b\t\t\tc\n\n\nd",
    "long-latin": (
        "the model identity probe measures a tokenizer fingerprint by "
        "differencing counts "
    )
    * 3,
}

#: How far a delta may drift and still count as agreement. Tokenizers are
#: deterministic, so in principle the tolerance is zero; it exists only so a
#: provider that pads a constant number of tokens onto every request does not
#: produce a false mismatch.
TOLERANCE = 0


@dataclass
class TokenizerSample:
    base: int | None = None
    deltas: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def measure(
    endpoint: Endpoint, probes: dict[str, str] | None = None, adapter=None
) -> TokenizerSample:
    """One call per probe plus one for BASE, all tiny."""
    probes = probes or PROBES
    sample = TokenizerSample()
    sample.base = _count(endpoint, BASE, adapter)
    if sample.base is None or sample.base <= 0:
        # A route that reports no input_tokens (or reports 0) has no countable
        # baseline; every delta off it would be noise. Say so rather than write
        # a fingerprint built on a zero.
        reason = "no positive usage.input_tokens for BASE"
        sample.errors.update({name: reason for name in probes})
        return sample
    for name, text in probes.items():
        counted = _count(endpoint, f"{BASE}{text}", adapter)
        if counted is None or counted <= 0:
            sample.errors[name] = "no positive usage.input_tokens in the response"
            continue
        delta = counted - sample.base
        if delta < 0:
            # BASE is a prefix of BASE+probe, so the probe can only add tokens.
            # A count below BASE means the server reported nothing useful for
            # this request (a stream that dropped its usage block, say);
            # recording the negative as a fingerprint would poison the signal.
            sample.errors[name] = (
                f"implausible input_tokens: {counted} < BASE {sample.base}"
            )
            continue
        sample.deltas[name] = delta
    return sample


def _effective_input_tokens(usage: dict) -> int | None:
    """The server's full input count for a request.

    ``input_tokens`` is only the *uncached* part. A provider that serves the
    BASE prefix from its prompt cache reports most of it under
    ``cache_read_input_tokens`` (and writes new cache under
    ``cache_creation_input_tokens``); reading only ``input_tokens`` would make
    the BASE and the BASE+probe count differ by a caching artefact rather than
    by the probe's own tokens. Sum them so the delta is the probe's bytes under
    the model's vocabulary, cached or not. A response with none of the three
    fields returns None (no countable value).
    """
    total = 0
    seen = False
    fields = (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    for key in fields:
        value = usage.get(key)
        if isinstance(value, int):
            total += value
            seen = True
    return total if seen else None


def _count(endpoint: Endpoint, text: str, adapter=None) -> int | None:
    completion = endpoint.complete(
        "Reply with the single word ok.",
        text,
        max_tokens=8,
        extra_body=adapter.extra_body if adapter else None,
        stream=True,
    )
    if completion.error is not None:
        return None
    return _effective_input_tokens(completion.usage or {})


def to_reference(sample: TokenizerSample) -> TokenizerReference:
    return TokenizerReference(base_input_tokens=sample.base, deltas=dict(sample.deltas))


def compare(
    sample: TokenizerSample, reference: TokenizerReference | None
) -> TokenizerEvidence:
    if reference is None or sample.base is None:
        return TokenizerEvidence(INSUFFICIENT, deltas=dict(sample.deltas))
    compared = 0
    disagreements: list[str] = []
    # The BASE count is itself a fingerprint of the fixed prefix's tokenisation
    # under this vocabulary; compare it too, not just the deltas.
    if reference.base_input_tokens is not None:
        compared += 1
        if abs(sample.base - reference.base_input_tokens) > TOLERANCE:
            disagreements.append(
                f"base: {sample.base} vs {reference.base_input_tokens}"
            )
    for name, expected in reference.deltas.items():
        got = sample.deltas.get(name)
        if got is None:
            continue
        compared += 1
        if abs(got - expected) > TOLERANCE:
            disagreements.append(f"{name}: {got} vs {expected}")
    if compared == 0:
        return TokenizerEvidence(
            INSUFFICIENT,
            deltas=dict(sample.deltas),
            reference_deltas=reference.deltas,
            base_input_tokens=sample.base,
            reference_base=reference.base_input_tokens,
        )
    verdict = MATCH if not disagreements else MISMATCH
    return TokenizerEvidence(
        verdict,
        deltas=dict(sample.deltas),
        reference_deltas=reference.deltas,
        base_input_tokens=sample.base,
        reference_base=reference.base_input_tokens,
    )
