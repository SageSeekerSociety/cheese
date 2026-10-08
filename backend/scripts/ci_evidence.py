"""Leave a file behind that names what a lost xdist worker was doing, and which
cases only passed on a retry.

Load with ``-p scripts.ci_evidence --ci-evidence-dir=DIR``.

pytest-timeout's thread method ends a wedged worker with ``os._exit(1)`` after
printing the stacks to the worker's own terminal, which xdist never forwards:
the run's log says ``[gw2] node down: Not properly terminated`` and nothing
else. So every process that runs tests keeps ``hang-<worker>.log``: one line
when a case starts and one when it ends, faulthandler's dump of every thread
just before the case's timeout fires, and faulthandler's dump on a fatal
signal. A worker that died mid-case is the one whose file ends on a ``start``.

``--reruns`` turns a failed or crashed case into a retry, and a case that then
passes leaves the JUnit report looking clean. The process that collects the
reports appends every retry to ``reruns.jsonl``, so ``scripts.assert_suite_ran``
can name those cases instead of letting them pass in silence.
"""

import faulthandler
import json
import time
from pathlib import Path

import pytest
import pytest_timeout

# How long before a case's timeout the stacks are dumped. pytest-timeout's
# timer calls os._exit at the deadline itself, so the dump has to come first;
# a case that finishes inside this margin leaves a dump followed by its `end`.
_DUMP_MARGIN_S = 0.5

_HANG_LOG = pytest.StashKey[object]()
# The case whose stacks are due: pytest-timeout cancels from more than one
# place (teardown and pytest_exception_interact), and only the first says `end`.
_PENDING = pytest.StashKey[str]()


def pytest_addoption(parser):
    group = parser.getgroup("cheese-ci")
    group.addoption(
        "--ci-evidence-dir",
        type=Path,
        help="write hang-<worker>.log and reruns.jsonl here",
    )


def _worker(config) -> str:
    return getattr(config, "workerinput", {}).get("workerid", "main")


def _hang_log(config):
    """This process's hang log, opened on first use; None without the option."""
    directory = config.getoption("--ci-evidence-dir")
    if directory is None:
        return None
    log = config.stash.get(_HANG_LOG, None)
    if log is None:
        directory.mkdir(parents=True, exist_ok=True)
        # Unbuffered binary, so what is written is on disk before an
        # os._exit can drop it; faulthandler writes to the descriptor.
        log = (directory / f"hang-{_worker(config)}.log").open("ab", buffering=0)
        faulthandler.enable(file=log, all_threads=True)
        config.stash[_HANG_LOG] = log
    return log


def _line(log, text: str) -> None:
    log.write(f"{time.strftime('%H:%M:%S')} {text}\n".encode())


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_set_timer(item, settings):
    log = _hang_log(item.config)
    if log is not None:
        _line(log, f"start {item.nodeid} timeout={settings.timeout}s")
        item.config.stash[_PENDING] = item.nodeid
        margin = min(_DUMP_MARGIN_S, settings.timeout / 10)
        faulthandler.dump_traceback_later(settings.timeout - margin, file=log)
    # None: pytest-timeout's own implementation still sets the real timer.
    return None


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_cancel_timer(item):
    log = item.config.stash.get(_HANG_LOG, None)
    if log is not None and item.config.stash.get(_PENDING, None) == item.nodeid:
        faulthandler.cancel_dump_traceback_later()
        del item.config.stash[_PENDING]
        _line(log, f"end {item.nodeid}")
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # rerunfailures retries inside one protocol. Its first failure cancels the
    # protocol's timer, so each later attempt needs its own setup/teardown timer.
    if getattr(item, "execution_count", 1) <= 1:
        return
    settings = pytest_timeout._get_item_settings(item)
    if settings.timeout and settings.timeout > 0 and not settings.func_only:
        item.ihook.pytest_timeout_cancel_timer(item=item)
        item.ihook.pytest_timeout_set_timer(item=item, settings=settings)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    try:
        return (yield)
    finally:
        if getattr(item, "execution_count", 1) > 1:
            item.ihook.pytest_timeout_cancel_timer(item=item)


class _RerunRecorder:
    """Appends every retry to ``reruns.jsonl``. Registered only in the process
    that does not run tests itself under xdist — or the only process without
    it — since every report reaches the controller too: each retry is written
    once."""

    def __init__(self, path: Path):
        self.path = path

    def pytest_runtest_logreport(self, report):
        if report.outcome != "rerun":
            return
        reason = str(report.longrepr or "").strip().splitlines()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as output:
            output.write(
                json.dumps(
                    {
                        "nodeid": report.nodeid,
                        # "???" is xdist's phase for a case whose worker died.
                        "crashed": report.when == "???",
                        "when": report.when,
                        "reason": reason[-1] if reason else "",
                    }
                )
                + "\n"
            )


def pytest_configure(config):
    directory = config.getoption("--ci-evidence-dir")
    if directory is not None and not hasattr(config, "workerinput"):
        config.pluginmanager.register(
            _RerunRecorder(directory / "reruns.jsonl"), "cheese-ci-reruns"
        )


def pytest_unconfigure(config):
    # The log stays open to the end: faulthandler holds its descriptor until
    # the process exits, and pytest's own faulthandler plugin restores stderr.
    if config.stash.get(_HANG_LOG, None) is not None:
        faulthandler.cancel_dump_traceback_later()
