"""Collecting a one-token fingerprint from an endpoint."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import battery
from .normalize import normalize_answer
from .stats import (
    DEFAULT_CONCURRENCY,
    DEFAULT_SAMPLES_PER_CELL,
    CellSamples,
)
from .transport import Endpoint, ReasoningAdapter


def probe_once(
    endpoint: Endpoint,
    cell_id: str,
    rng: random.Random,
    adapter: ReasoningAdapter | None = None,
) -> CellSamples:
    """One request against one cell, normalised into a single sample."""
    prompt = battery.pick_paraphrase(cell_id, rng)
    system = battery.system_prompt(cell_id)
    kwargs: dict = {}
    if adapter is not None:
        kwargs["max_tokens"] = adapter.max_tokens
        kwargs["extra_body"] = adapter.extra_body
        kwargs["temperature"] = adapter.temperature
    completion = endpoint.complete(system, prompt, **kwargs)
    samples = CellSamples()
    if completion.error is not None:
        samples.add("error", None)
        return samples
    answer = normalize_answer(completion.text, battery.task_spec(cell_id).domain)
    samples.add(answer.category, answer.normalized, completion.usage)
    return samples


def collect(
    endpoint: Endpoint,
    cells: tuple[str, ...] | list[str],
    samples_per_cell: int = DEFAULT_SAMPLES_PER_CELL,
    concurrency: int = DEFAULT_CONCURRENCY,
    seed: int = 20261005,
    progress=None,
    adapter: ReasoningAdapter | None = None,
) -> dict[str, CellSamples]:
    """Fill every cell with ``samples_per_cell`` samples.

    Requests are issued concurrently but each cell's samples are gathered
    independently, so a rate-limited cell degrades to fewer valid samples
    instead of poisoning the run. Results are merged in *completion* order
    (``as_completed``), which is what keeps each cell's ``order`` list -- the
    arrival order ``split_half_jsd`` reads -- honest.
    """
    jobs: list[tuple[str, int]] = [
        (cell, i) for cell in cells for i in range(samples_per_cell)
    ]
    # A per-job RNG keeps each request's paraphrase choice independent of the
    # order the pool happens to complete in.
    per_job = {job: random.Random(seed * 1000003 + i) for i, job in enumerate(jobs)}

    out: dict[str, CellSamples] = {cell: CellSamples() for cell in cells}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(probe_once, endpoint, cell, per_job[(cell, i)], adapter): cell
            for cell, i in jobs
        }
        for future in as_completed(futures):
            cell = futures[future]
            try:
                _merge(out[cell], future.result())
            except Exception:  # a crashed probe is a lost sample, not a lost run
                out[cell].error += 1
            done += 1
            if progress is not None:
                progress(cell, done, len(jobs))
    return out


def _merge(target: CellSamples, source: CellSamples) -> None:
    target.counts.update(source.counts)
    target.valid += source.valid
    target.invalid += source.invalid
    target.refusal += source.refusal
    target.empty += source.empty
    target.error += source.error
    target.input_tokens.extend(source.input_tokens)
    target.output_tokens.extend(source.output_tokens)
    target.order.extend(source.order)


def collect_sequential(
    endpoint: Endpoint,
    cells,
    samples_per_cell: int = DEFAULT_SAMPLES_PER_CELL,
    seed: int = 20261005,
    adapter: ReasoningAdapter | None = None,
) -> dict[str, CellSamples]:
    rng = random.Random(seed)
    out = {cell: CellSamples() for cell in cells}
    for cell in cells:
        for _ in range(samples_per_cell):
            _merge(out[cell], probe_once(endpoint, cell, rng, adapter))
    return out
