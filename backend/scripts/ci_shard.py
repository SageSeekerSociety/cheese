"""Partition the selected pytest cases across independent CI runners.

Load with ``-p scripts.ci_shard --ci-shard=0/4``. xdist still schedules the
assigned cases within each runner; every worker derives the same partition.
"""

import hashlib
import json
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
    items[:] = assigned
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
