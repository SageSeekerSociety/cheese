"""The model-identity probe's pure halves: normalisation, distance, verdict.

No network, no database: everything here is the arithmetic and the folding
rules that a live comparison depends on. The parts that talk to a model are
exercised by running the CLI, not by a unit test.
"""

from __future__ import annotations

import random

import pytest

from scripts.model_identity_probe import battery
from scripts.model_identity_probe import reference as reference_mod
from scripts.model_identity_probe.normalize import (
    normalize_answer,
    parse_any_number,
    parse_chinese_numeral,
    parse_english_number_word,
)
from scripts.model_identity_probe.stats import (
    JSD_MATCH_THRESHOLD,
    JSD_MISMATCH_THRESHOLD,
    CellSamples,
    compare_cells,
    jensen_shannon_divergence,
    mean_jsd,
    shannon_entropy_bits,
    split_half_jsd,
)
from scripts.model_identity_probe.tokenizer_fp import (
    BASE as TOKENIZER_BASE,
)
from scripts.model_identity_probe.tokenizer_fp import (
    PROBES as TOKENIZER_PROBES,
)
from scripts.model_identity_probe.tokenizer_fp import TokenizerSample
from scripts.model_identity_probe.tokenizer_fp import compare as compare_tokens
from scripts.model_identity_probe.tokenizer_fp import (
    measure as measure_tokens,
)
from scripts.model_identity_probe.transport import (
    Completion,
    evidence_headers,
    pool_from_headers,
)
from scripts.model_identity_probe.verdict import (
    INSUFFICIENT,
    MATCH,
    MISMATCH,
    UNCERTAIN,
    BehaviourEvidence,
    ProvenanceEvidence,
    TokenizerEvidence,
    combine,
    decide_jsd_verdict,
)

INT_1_100 = battery.PROBE_TASKS["random-number-1-100"].domain
COIN = battery.PROBE_TASKS["coin-flip"].domain
COLOR = battery.PROBE_TASKS["random-color"].domain


# --- normalisation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7", 7),
        ("47", 47),
        ("seventy", 70),
        ("fortyseven", 47),
        ("onehundred", 100),
        ("四十二", 42),
        ("十五", 15),
        ("十", 10),
        ("两", 2),
    ],
)
def test_numbers_parse_through_every_spelling(raw: str, expected: int) -> None:
    assert parse_any_number(raw) == expected


def test_non_numerals_do_not_parse() -> None:
    assert parse_any_number("blue") is None
    assert parse_chinese_numeral("蓝") is None
    assert parse_english_number_word("blue") is None


def test_a_hyphenated_number_word_folds_once_punctuation_is_stripped() -> None:
    # The normaliser strips the hyphen; parse_* alone sees only the joined word.
    assert normalize_answer("forty-seven", INT_1_100).normalized == "47"


@pytest.mark.parametrize("raw", ["４２", "٤٢", "۴۲"])
def test_fullwidth_and_indic_digits_fold_to_latin(raw: str) -> None:
    assert normalize_answer(raw, INT_1_100).normalized == "42"


def test_punctuation_quotes_and_case_are_stripped() -> None:
    assert normalize_answer('"Seventy."', INT_1_100).normalized == "70"
    assert normalize_answer("BLUE!", COLOR).normalized == "blue"


def test_out_of_domain_number_is_invalid_not_silently_clamped() -> None:
    answer = normalize_answer("500", INT_1_100)
    assert answer.category == "invalid"
    assert answer.normalized == "500"


def test_refusals_are_recognised_in_both_languages() -> None:
    assert normalize_answer("I cannot help with that.", INT_1_100).category == "refusal"
    assert normalize_answer("我不能回答这个问题", INT_1_100).category == "refusal"


def test_empty_and_emoji_only_answers_are_empty() -> None:
    assert normalize_answer("", INT_1_100).category == "empty"
    assert normalize_answer("   ", INT_1_100).category == "empty"
    assert normalize_answer("🎈", INT_1_100).category == "empty"


def test_colour_and_coin_aliases_fold() -> None:
    assert normalize_answer("grey", COLOR).normalized == "gray"
    assert normalize_answer("蓝色", COLOR).normalized == "蓝"
    assert normalize_answer("正", COIN).normalized == "heads"
    assert normalize_answer("heads", COIN).normalized == "heads"
    assert normalize_answer("tails", COIN).normalized == "tails"


def test_letter_names_fold_and_letter_domain_validates() -> None:
    letter = battery.PROBE_TASKS["random-letter"].domain
    assert normalize_answer("queue", letter).normalized == "q"
    assert normalize_answer("k", letter).category == "valid"
    assert normalize_answer("xyz", letter).category == "invalid"


# --- distance ----------------------------------------------------------------


def test_jsd_of_a_distribution_with_itself_is_zero() -> None:
    counts = {"47": 5, "42": 3, "seven": 2}
    assert jensen_shannon_divergence(counts, counts) == pytest.approx(0.0, abs=1e-12)


def test_jsd_of_disjoint_supports_is_one_bit() -> None:
    assert jensen_shannon_divergence({"a": 4}, {"b": 4}) == pytest.approx(1.0)


def test_jsd_is_symmetric_and_bounded() -> None:
    left = {"a": 7, "b": 1}
    right = {"a": 2, "c": 5}
    forward = jensen_shannon_divergence(left, right)
    assert forward == pytest.approx(jensen_shannon_divergence(right, left))
    assert 0.0 <= forward <= 1.0


def test_jsd_ignores_an_empty_side() -> None:
    assert jensen_shannon_divergence({}, {"a": 3}) == 0.0


def test_entropy_of_a_single_outcome_is_zero() -> None:
    assert shannon_entropy_bits({"x": 9}) == 0.0
    assert shannon_entropy_bits({"x": 4, "y": 4}) == pytest.approx(1.0)


def test_compare_cells_skips_cells_either_side_lacks_samples_for() -> None:
    reference = {"c1": _cell({"a": 12}), "c2": _cell({"b": 3})}
    target = {"c1": _cell({"a": 12}), "c2": _cell({"b": 12})}
    entries = compare_cells(reference, target)
    assert [entry.cell_id for entry in entries] == ["c1"]
    assert entries[0].valid_a == 12
    assert entries[0].valid_b == 12
    assert mean_jsd(entries) == pytest.approx(0.0, abs=1e-12)


def test_compare_cells_only_sees_cells_the_reference_covers() -> None:
    entries = compare_cells({"c1": _cell({"a": 12})}, {"c9": _cell({"a": 12})})
    assert entries == []


def test_split_half_flags_a_cell_that_is_two_distributions() -> None:
    stable = _cell({"a": 10, "b": 10})
    mixed = _cell({"a": 10, "b": 0})
    assert split_half_jsd(stable) == pytest.approx(0.0, abs=1e-9)
    assert split_half_jsd(mixed) is not None
    assert split_half_jsd(_cell({"a": 3})) is None  # too few for two halves


# --- verdict -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("mean", "cells", "expected"),
    [
        (0.10, 8, MATCH),
        (JSD_MATCH_THRESHOLD, 8, MATCH),
        (0.30, 8, UNCERTAIN),
        (JSD_MISMATCH_THRESHOLD, 8, UNCERTAIN),
        (0.44, 8, MISMATCH),
        (0.05, 2, INSUFFICIENT),  # too few comparable cells
        (None, 8, INSUFFICIENT),
    ],
)
def test_decide_jsd_verdict_bands(mean, cells, expected) -> None:
    assert decide_jsd_verdict(mean, cells) == expected


def test_a_wire_name_that_is_not_the_claim_is_a_mismatch_on_its_own() -> None:
    provenance = _provenance(MISMATCH)
    behaviour = _behaviour(MATCH, 8)
    verdict, reason = combine(provenance, behaviour, None)
    assert verdict == MISMATCH
    assert "different model than the one claimed" in reason


def test_behaviour_disagreeing_with_the_reference_is_a_mismatch() -> None:
    verdict, _ = combine(_provenance(MATCH), _behaviour(MISMATCH, 8), None)
    assert verdict == MISMATCH


def test_matching_behaviour_with_a_foreign_tokenizer_is_only_uncertain() -> None:
    verdict, reason = combine(
        _provenance(MATCH), _behaviour(MATCH, 8), TokenizerEvidence(MISMATCH)
    )
    assert verdict == UNCERTAIN
    assert "tokenizer" in reason


def test_matching_behaviour_and_tokenizer_is_a_match() -> None:
    verdict, _ = combine(
        _provenance(MATCH), _behaviour(MATCH, 8), TokenizerEvidence(MATCH)
    )
    assert verdict == MATCH


def test_no_reference_leaves_the_verdict_insufficient() -> None:
    verdict, _ = combine(_provenance(MATCH), None, None)
    assert verdict == INSUFFICIENT


def test_uncertainty_band_is_reported_as_uncertain() -> None:
    verdict, _ = combine(
        _provenance(MATCH), _behaviour(UNCERTAIN, 8), TokenizerEvidence(MATCH)
    )
    assert verdict == UNCERTAIN


# --- provenance --------------------------------------------------------------


def test_the_gateway_and_anthropic_shells_are_told_apart() -> None:
    assert pool_from_headers({"x-litellm-call-id": "abc"}) == "gateway"
    assert (
        pool_from_headers({"anthropic-ratelimit-requests-limit": "50"})
        == "subscription"
    )
    assert pool_from_headers({"request-id": "req_1"}) == "subscription"
    assert pool_from_headers({"content-type": "application/json"}) is None


def test_only_evidence_headers_survive_the_filter() -> None:
    kept = evidence_headers(
        {
            "x-litellm-model-name": "openai/gpt-6-astra",
            "anthropic-ratelimit-tokens-remaining": "9",
            "cf-ray": "abc123",
            "content-type": "application/json",
            "set-cookie": "secret",
        }
    )
    assert set(kept) == {
        "x-litellm-model-name",
        "anthropic-ratelimit-tokens-remaining",
        "cf-ray",
    }


# --- tokenizer ---------------------------------------------------------------


def test_tokenizer_deltas_that_disagree_are_a_mismatch() -> None:
    reference = reference_mod.TokenizerReference(
        base_input_tokens=10, deltas={"latin": 9}
    )
    agreed = compare_tokens(TokenizerSample(base=10, deltas={"latin": 9}), reference)
    differed = compare_tokens(TokenizerSample(base=10, deltas={"latin": 11}), reference)
    assert agreed.verdict == MATCH
    assert differed.verdict == MISMATCH


def test_a_probe_measured_below_base_is_dropped_not_recorded() -> None:
    # BASE is a prefix of BASE+probe, so input_tokens can only grow. A server
    # that reports a smaller count for the longer prompt (a stream that dropped
    # its usage block) must not be written into the fingerprint as a negative.
    def usage_for(prompt: str) -> int:
        if prompt == TOKENIZER_BASE:
            return 18
        if prompt == TOKENIZER_BASE + TOKENIZER_PROBES["latin"]:
            return 27
        if prompt == TOKENIZER_BASE + TOKENIZER_PROBES["digits"]:
            return 0
        return 18

    class _Stub:
        def complete(self, system: str, prompt: str, **kwargs: object) -> Completion:
            return Completion(
                "ok", "stub", {"input_tokens": usage_for(prompt)}, {}, 0.0
            )

    sample = measure_tokens(_Stub())  # type: ignore[arg-type]
    assert sample.deltas["latin"] == 9
    assert "digits" not in sample.deltas
    assert "digits" in sample.errors


def test_tokenizer_without_a_reference_is_insufficient() -> None:
    assert (
        compare_tokens(TokenizerSample(base=10, deltas={"latin": 9}), None).verdict
        == INSUFFICIENT
    )


# --- battery and reference store --------------------------------------------


def test_preset_cells_are_a_prefix_of_the_priority_order() -> None:
    assert battery.cells_for_preset("quick") == battery.CELL_PRIORITY_ORDER[:4]
    assert len(battery.cells_for_preset("strict")) == 16


def test_every_cell_id_in_the_order_is_real() -> None:
    assert all(battery.is_cell_id(cell) for cell in battery.CELL_PRIORITY_ORDER)
    assert not battery.is_cell_id("random-number-1-100:fr")
    assert not battery.is_cell_id("nonsense:en")


def test_paraphrases_come_from_that_cell_only() -> None:
    rng = random.Random(7)
    task, lang = battery.parse_cell_id("random-color:zh")
    pool = set(battery.PROBE_TASKS[task].paraphrases[lang])
    assert {battery.pick_paraphrase("random-color:zh", rng) for _ in range(50)} <= pool


def test_a_reference_survives_a_round_trip(tmp_path) -> None:
    reference = reference_mod.new_reference(
        "demo-model", "gateway:demo", samples_per_cell=4
    )
    reference.cells = {"random-color:en": _cell({"blue": 4, "red": 1})}
    reference.tokenizer = reference_mod.TokenizerReference(
        base_input_tokens=8, deltas={"latin": 7}
    )
    path = reference_mod.save(reference, tmp_path)
    loaded = reference_mod.load("demo-model", tmp_path)
    assert loaded is not None
    assert loaded.cells["random-color:en"].counts["blue"] == 4
    assert loaded.tokenizer is not None and loaded.tokenizer.deltas == {"latin": 7}
    assert path.name == "demo-model.json"


def test_an_absent_reference_loads_as_none(tmp_path) -> None:
    assert reference_mod.load("nobody", tmp_path) is None


def test_model_ids_that_are_not_filenames_are_slugged(tmp_path) -> None:
    assert (
        reference_mod.path_for("gpt-6.1-sol/x", tmp_path).name == "gpt-6.1-sol_x.json"
    )


# --- helpers -----------------------------------------------------------------


def _cell(counts: dict[str, int]) -> CellSamples:
    samples = CellSamples()
    for value, count in counts.items():
        for _ in range(count):
            samples.add("valid", value)
    return samples


def _behaviour(verdict: str, cells: int) -> BehaviourEvidence:
    return BehaviourEvidence(mean_jsd=0.2, comparable_cells=cells, verdict=verdict)


def _provenance(verdict: str) -> ProvenanceEvidence:
    return ProvenanceEvidence(
        claimed_model="m",
        wire_model="m",
        upstream_model_name=None,
        pool="gateway",
        echo_matches_claim=verdict == MATCH,
        verdict=verdict,
    )
