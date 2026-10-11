"""In-run audit of ci:fast's backend-pure selection.

Loaded with ``-p`` on the one pytest run the fast tier makes, it records what
that run itself decided about the selection, so the runner does not have to
guess from a report written after the fact:

  * ``selected``  — every node left after ``-m``/``-k`` filtering, with the
    fixture-derived layer marker conftest put on it. The pure ones are the set
    this tier promises to run.
  * ``deselected`` — every node those filters removed, with its layer. An
    excluded ``integration``/``contract`` node is the tier working as designed;
    an excluded ``pure`` node is the layer being silently under-run, and the
    runner fails it rather than letting a ``-k`` hide it.
  * ``executed``  — every node that reported, with its outcome.

``pytest_collection_modifyitems`` is ``trylast`` so it runs after conftest has
added the layer markers and after pytest has applied the ``-m``/``-k`` filters;
without it the layers would not be on the items yet. The file is written with a
temp-and-rename so a reader never sees a half-written audit.

Plain stdlib and never raising: a plugin that fails to import would turn an
otherwise valid run into a usage error (pytest exit 4, reported unknown), so its
own job must not be what breaks a run.
"""

import json
import os

try:
    import pytest
except ImportError:  # pragma: no cover - pytest is the process that loads us
    pytest = None

_LAYERS = ("pure", "contract", "integration")
_OUT = os.environ.get("CI_FAST_NODES")
_STATE = {"selected": [], "deselected": [], "executed": []}


def _layer(item):
    for name in _LAYERS:
        if item.get_closest_marker(name) is not None:
            return name
    return None


def _flush():
    if not _OUT:
        return
    try:
        with open(_OUT + ".tmp", "w") as handle:
            json.dump(_STATE, handle)
        os.replace(_OUT + ".tmp", _OUT)
    except OSError:
        pass


def pytest_deselected(items):
    for item in items:
        _STATE["deselected"].append({"node": item.nodeid, "layer": _layer(item)})
    _flush()


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        _STATE["selected"].append({"node": item.nodeid, "layer": _layer(item)})
    _flush()


def pytest_runtest_logreport(report):
    # A call that passed, plus anything that did not pass at all (setup skips,
    # errors, teardown failures) — everything the executed set must account for.
    if report.when == "call" or report.outcome != "passed":
        _STATE["executed"].append(
            {"node": report.nodeid, "when": report.when, "outcome": report.outcome}
        )


def pytest_sessionfinish(session, exitstatus):
    _flush()


if pytest is not None:
    pytest_collection_modifyitems = pytest.hookimpl(trylast=True)(pytest_collection_modifyitems)
