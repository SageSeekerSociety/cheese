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
  * ``executed``  — every node that reported, with its outcome, and for a
    skip the reason pytest gave: the reader counts only a skip that names a
    capability this machine lacks as reached.

Under ``-n`` the run is several processes. Each xdist worker sees the whole
collection (``pytest_collection_modifyitems`` runs before distribution) and
only its own reports, so a single shared file would be overwritten by
whichever worker finished last: the reader would then take that worker's
selection for the run and report every other worker's tests as never
executed. A worker therefore writes ``<sidecar>.<workerid>.json``, and the
controller — the one process without ``workerinput`` — merges the parts after
the workers exit. A plain run keeps writing the one file, exactly as before.

``pytest_collection_modifyitems`` is ``trylast`` so it runs after conftest has
added the layer markers and after pytest has applied the ``-m``/``-k`` filters;
without it the layers would not be on the items yet. The file is written with a
temp-and-rename so a reader never sees a half-written audit.

Plain stdlib and never raising: a plugin that fails to import would turn an
otherwise valid run into a usage error (pytest exit 4, reported unknown), so its
own job must not be what breaks a run.
"""

import glob
import json
import os

try:
    import pytest
except ImportError:  # pragma: no cover - pytest is the process that loads us
    pytest = None

_LAYERS = ("pure", "contract", "integration")
_OUT = os.environ.get("CI_FAST_NODES")
_STATE = {"selected": [], "deselected": [], "executed": []}
#: The xdist worker id, or None in a plain run and in the xdist controller.
_WORKER = None


def pytest_configure(config):
    """Note which xdist worker this process is, so the workers do not collide.

    A worker's config carries ``workerinput``; the controller's does not. A
    plain run has neither and keeps writing the one sidecar.
    """
    global _WORKER
    workerinput = getattr(config, "workerinput", None) if config is not None else None
    _WORKER = (workerinput.get("workerid") or workerinput.get("id")
               if isinstance(workerinput, dict) else None)


def _write(state, path):
    try:
        with open(path + ".tmp", "w") as handle:
            json.dump(state, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def _flush():
    if not _OUT:
        return
    _write(_STATE, _OUT if _WORKER is None else f"{_OUT}.{_WORKER}.json")


def _dedupe(rows, key):
    """Keep the last record per key; every worker may name the same node."""
    seen, out = set(), []
    for row in reversed(rows):
        marker = key(row)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(row)
    out.reverse()
    return out


def _merge_parts():
    """Fold the xdist workers' parts, and the controller's own state, into one.

    Only the controller calls this: it runs after the workers exit, so every
    part is complete. A part that cannot be read is left out rather than
    guessed at; the nodes it held then read as never executed, which is
    unknown, never a pass.
    """
    if not _OUT:
        return
    directory, base = os.path.split(_OUT)
    parts = sorted(glob.glob(os.path.join(directory, base + ".*.json")))
    if not parts:
        return
    merged = {"selected": [], "deselected": [], "executed": []}
    for part in parts:
        try:
            with open(part) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in merged:
            rows = data.get(key)
            if isinstance(rows, list):
                merged[key].extend(row for row in rows if isinstance(row, dict))
    # The controller also receives every report the workers send; its own
    # state is the same evidence and is folded in so a worker whose part was
    # lost still counts for what it reported.
    for key in merged:
        merged[key].extend(_STATE.get(key, []))
    merged["selected"] = _dedupe(merged["selected"], lambda row: row.get("node"))
    merged["deselected"] = _dedupe(merged["deselected"], lambda row: row.get("node"))
    merged["executed"] = _dedupe(merged["executed"],
                                 lambda row: (row.get("node"), row.get("when")))
    _write(merged, _OUT)
    for part in parts:
        try:
            os.unlink(part)
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


def _skip_reason(report):
    """What pytest said about a skip, so the reader can tell which kind it was.

    A skip that names a capability this machine lacks (bubblewrap, sandbox-exec,
    ripgrep, tmux) is the environment declining the test. Any other skip — a
    plain ``@pytest.mark.skip``, a feature-flag ``skipif``, an xfail — may hide
    a real failure, so it must keep reading as not executed. ``wasxfail`` is
    set only for an xfail failure; pytest reports it with outcome ``skipped``.
    """
    wasxfail = getattr(report, "wasxfail", None)
    if wasxfail is not None:
        return f"xfail: {wasxfail}"
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) >= 3:
        return str(longrepr[2])
    return str(longrepr)


def pytest_runtest_logreport(report):
    # A call that passed, plus anything that did not pass at all (setup skips,
    # errors, teardown failures) — everything the executed set must account for.
    if report.when == "call" or report.outcome != "passed":
        entry = {"node": report.nodeid, "when": report.when, "outcome": report.outcome}
        if report.outcome == "skipped":
            entry["reason"] = _skip_reason(report)
        _STATE["executed"].append(entry)


def pytest_sessionfinish(session, exitstatus):
    _flush()
    if _WORKER is None:
        _merge_parts()


def _layer(item):
    for name in _LAYERS:
        if item.get_closest_marker(name) is not None:
            return name
    return None


if pytest is not None:
    pytest_collection_modifyitems = pytest.hookimpl(trylast=True)(pytest_collection_modifyitems)
