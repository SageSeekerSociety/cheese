"""The server holds the middle generation longer, so full collections — which
walk the whole live heap with the event loop held — run less often
(`core/gc_tuning`)."""

import gc
import importlib
import threading

import pytest

from app.core import loop_stalls


def _tuning():
    return importlib.import_module("app.core.gc_tuning")


@pytest.fixture
def interpreter_thresholds():
    before = gc.get_threshold()
    yield before
    gc.set_threshold(*before)


def test_the_policy_raises_the_middle_generation_and_leaves_the_others(
    interpreter_thresholds, monkeypatch
):
    tuning = _tuning()
    monkeypatch.setattr(tuning, "_done", False)

    assert tuning.tune_collector(serving=True, on_main_thread=True) is True

    gen0, gen1, gen2 = gc.get_threshold()
    # Generation 1 is the one that decides how often the middle generation is
    # promoted; raising it is the whole change.
    assert gen1 == tuning.GEN1_THRESHOLD > interpreter_thresholds[1]
    # Generations 0 and 2 stay the interpreter's own: generation 0's default
    # has already moved between builds of 3.13, and pinning it would change how
    # often the young heap is walked, which nothing measured asked for.
    assert (gen0, gen2) == (interpreter_thresholds[0], interpreter_thresholds[2])


def test_only_a_serving_process_on_the_main_thread_is_tuned(
    interpreter_thresholds, monkeypatch
):
    tuning = _tuning()
    monkeypatch.setattr(tuning, "_done", False)

    assert tuning.tune_collector(serving=False, on_main_thread=True) is False
    assert tuning.tune_collector(serving=True, on_main_thread=False) is False
    assert gc.get_threshold() == interpreter_thresholds


def test_a_serving_process_is_tuned_once(interpreter_thresholds, monkeypatch):
    tuning = _tuning()
    monkeypatch.setattr(tuning, "_done", False)

    assert tuning.tune_collector(serving=True, on_main_thread=True) is True
    tuned = gc.get_threshold()
    # A second call must not stack another change on top (and returns False so
    # a caller can tell it did nothing).
    assert tuning.tune_collector(serving=True, on_main_thread=True) is False
    assert gc.get_threshold() == tuned


def test_importing_the_app_in_a_test_does_not_tune_the_process(
    interpreter_thresholds, monkeypatch
):
    # The suite takes every measurement it has (timing, GC counts, heap sizes)
    # under the interpreter's own policy. Only a process `app/serve.py` started
    # is tuned, so importing the app here must leave this one alone.
    tuning = _tuning()
    monkeypatch.delenv(tuning.SERVING, raising=False)
    monkeypatch.setattr(tuning, "_done", False)

    assert tuning.tune_after_startup() is False
    assert gc.get_threshold() == interpreter_thresholds

    # Even the server's variable is not enough off the main thread.
    monkeypatch.setenv(tuning.SERVING, "1")
    seen = {}
    off_main = threading.Thread(
        target=lambda: seen.update(froze=tuning.tune_after_startup())
    )
    off_main.start()
    off_main.join()
    assert seen == {"froze": False}
    assert gc.get_threshold() == interpreter_thresholds


def test_the_performance_board_says_which_policy_the_process_runs(monkeypatch):
    monkeypatch.setattr(gc, "get_threshold", lambda: (2000, 100, 10))
    status = loop_stalls.stall_status()
    assert status["thresholds"] == [2000, 100, 10]
