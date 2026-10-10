"""Partition the selected pytest cases across independent CI runners.

Load with ``-p scripts.ci_shard --ci-shard=0/4``. xdist still schedules the
assigned cases within each runner; every worker derives the same partition.

The assigned cases are then ordered module by module, the longest module first
by ``tests/module_durations.json`` (refreshed with ``scripts.test_durations``),
each module's cases kept together in their collected order. Under
``--dist loadfile --no-loadscope-reorder`` a worker takes the next module from
the head of that order whenever it runs low, so the run ends on short modules
instead of one worker finishing a long one alone.
"""

import hashlib
import json
import statistics
from pathlib import Path

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("cheese-ci")
    group.addoption("--ci-shard", default="0/1", help="zero-based index/count")
    group.addoption("--ci-selection-output", type=Path)


def shard_spec(value):
    try:
        index, count = map(int, value.split("/"))
    except ValueError as exc:
        raise pytest.UsageError("--ci-shard must be index/count") from exc
    if not 0 <= index < count:
        raise pytest.UsageError("--ci-shard requires 0 <= index < count")
    return index, count


def pytest_configure(config):
    shard_spec(config.getoption("--ci-shard"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Finish layer assignment and normal -m/-k filtering before partitioning.
    result = yield
    index, count = shard_spec(config.getoption("--ci-shard"))
    selected = [item.nodeid for item in items]
    assigned, deselected = [], []
    for item in items:
        bucket = int.from_bytes(hashlib.sha256(item.nodeid.encode()).digest()) % count
        (assigned if bucket == index else deselected).append(item)
    # The manifest below records which cases this shard holds, in partition
    # order, which is what assert_suite_ran recomputes; only the run is reordered.
    items[:] = _longest_module_first(assigned)
    for item in assigned:
        item.user_properties.append(("cheese_nodeid", item.nodeid))
    config.hook.pytest_deselected(items=deselected)
    output = config.getoption("--ci-selection-output")
    if output:
        worker = getattr(config, "workerinput", {}).get("workerid", "main")
        output.mkdir(parents=True, exist_ok=True)
        (output / f"selection-{worker}.json").write_text(
            json.dumps(
                {
                    "shard_index": index,
                    "shard_count": count,
                    "markexpr": config.option.markexpr,
                    "keyword": config.option.keyword,
                    "selected": selected,
                    "assigned": [item.nodeid for item in assigned],
                },
                indent=2,
            )
            + "\n"
        )
    return result


DURATIONS = Path(__file__).resolve().parents[1] / "tests" / "module_durations.json"
LAYERS = ("pure", "contract", "integration")


def _layer(item):
    return next((m.name for m in item.iter_markers() if m.name in LAYERS), None)


def _longest_module_first(items):
    """``items`` grouped by module, longest estimated module first.

    A module's estimate is its recorded seconds per test times the cases this
    shard holds of it. A module with no record is estimated at its layer's
    median seconds per test. Ties keep collection order, and so do the cases
    within a module.
    """
    if not DURATIONS.exists() or not items:
        return items
    recorded = json.loads(DURATIONS.read_text())
    modules: dict[str, list] = {}
    for item in items:
        modules.setdefault(item.nodeid.split("::")[0], []).append(item)

    def estimate(module, cases):
        layer = recorded.get(_layer(cases[0]) or "", {})
        seconds, tests = layer.get(module, (None, None))
        if seconds is None or not tests:
            per_test = [s / n for s, n in layer.values() if n] or [0.0]
            return statistics.median(per_test) * len(cases)
        return seconds / tests * len(cases)

    order = sorted(modules.items(), key=lambda kv: -estimate(*kv))
    return [item for _, cases in order for item in cases]
