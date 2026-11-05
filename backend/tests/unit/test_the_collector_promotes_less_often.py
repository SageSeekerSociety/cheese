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


def test_the_policy_raises_both_young_generations_and_leaves_the_full_pass_alone(
    interpreter_thresholds, monkeypatch
):
    tuning = _tuning()
    monkeypatch.setattr(tuning, "_done", False)

    assert tuning.tune_collector(serving=True, on_main_thread=True) is True

    gen0, gen1, gen2 = gc.get_threshold()
    # Generation 0's threshold decides how often the young heap is walked, and
    # generation 1's how often its survivors are promoted; raising both is the
    # change. Generation 0 is a floor, so the target is the floor and not the
    # constant: a build that already walks more of the young heap keeps its own
    # value (pinned by the test below). Generation 1 is set outright.
    assert gen0 == max(interpreter_thresholds[0], tuning.GEN0_THRESHOLD)
    assert gen1 == tuning.GEN1_THRESHOLD > interpreter_thresholds[1]
    # Generation 2's threshold decides nothing in 3.13 — a full pass is gated on
    # the promotion ratio — so it stays whatever the interpreter started with.
    assert gen2 == interpreter_thresholds[2]


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


def test_a_build_that_already_walks_more_of_the_young_heap_is_left_alone(
    interpreter_thresholds, monkeypatch
):
    # The interpreter's generation-0 default has moved before (700 -> 2000) and
    # may move again. Where a build already walks more of it than our floor, the
    # floor must not tune it down — that would collect the young heap more often
    # than the build chose, the opposite of what this module exists for.
    tuning = _tuning()
    monkeypatch.setattr(tuning, "_done", False)
    gc.set_threshold(tuning.GEN0_THRESHOLD * 2, 10, 10)

    assert tuning.tune_collector(serving=True, on_main_thread=True) is True

    gen0, gen1, _gen2 = gc.get_threshold()
    assert gen0 == tuning.GEN0_THRESHOLD * 2, "a higher interpreter value was lowered"
    assert gen1 == tuning.GEN1_THRESHOLD
