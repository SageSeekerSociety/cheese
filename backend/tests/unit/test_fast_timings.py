"""Holds ``tests/support/fast_timings.py``'s table to the tree.

The failure this guards is quiet in both directions. A knob renamed or moved
leaves the table pinning a name nobody reads: the fixture still "works", the
test still passes, and the wait it was supposed to remove comes back as seconds
of the layer's budget. A knob pinned in a module that merely *imports* it is the
same failure with a straight face — ``monkeypatch.setattr`` succeeds, and the
reader in the other module goes on using the fast binding it copied at import
time. So each entry is checked against the module's own source, not against
``hasattr``.
"""

import ast
import importlib
from pathlib import Path

import pytest

from app.core.config import Settings, settings
from tests.support.fast_timings import FAST_S, MODULE_KNOBS, SETTINGS_KNOBS

APP = Path(__file__).resolve().parents[2] / "app"


def _module_file(dotted: str) -> Path:
    return APP / Path(*dotted.removeprefix("app.").split(".")).with_suffix(".py")


def _assigned_at_module_level(path: Path) -> set[str]:
    """Names the source gives a value at module level (``X = ...`` / ``X: T = ...``)."""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize(("module_name", "name"), MODULE_KNOBS)
def test_each_knob_is_the_own_binding_of_the_module_reads_it(module_name, name):
    """The module that reads the name must also be the one that assigns it."""
    path = _module_file(module_name)
    assert path.is_file(), f"{module_name} 不是 app 下的模块 —— 表里这项点错了地方"
    assert name in _assigned_at_module_level(path), (
        f"{name} 不是 {module_name} 自己赋的：补在这里，读它的地方（多半在别的"
        f"模块，或在导入时就算进另一个常量）不会跟着变。改到真正持有它的模块上"
    )


@pytest.mark.parametrize(("name", "_value"), SETTINGS_KNOBS)
def test_each_settings_knob_is_a_declared_field(name, _value):
    assert name in Settings.model_fields, (
        f"settings.{name} 不在 Settings 里了 —— 改名或删掉时把表里这一项一起改"
    )


def test_the_fixture_moves_every_binding_it_claims(fast_timings):
    for module_name, name in MODULE_KNOBS:
        module = importlib.import_module(module_name)
        assert getattr(module, name) == FAST_S, f"{module_name}.{name} 没被压到毫秒级"
    for name, value in SETTINGS_KNOBS:
        assert getattr(settings, name) == value, f"settings.{name} 没被压到 {value}"


def test_every_value_is_millisecond_scale(fast_timings):
    """A table entry that is still seconds long would pass the test above."""
    assert fast_timings.applied
    slow = {label: value for label, value in fast_timings.applied.items() if value > 1}
    assert not slow, f"这些还是秒级：{slow} —— 要的是毫秒级等待，不是调小一点"


def test_nothing_leaks_past_the_test():
    """The undo is monkeypatch's; a table applied to the wrong object would not undo."""
    module = importlib.import_module("app.domain.review.events")
    module_before = module.RECONNECT_DELAY_S
    settings_before = settings.turn_silence_timeout_s
    with pytest.MonkeyPatch.context() as patch:
        from tests.support.fast_timings import FastTimings

        FastTimings(patch).apply()
        assert module.RECONNECT_DELAY_S == FAST_S
        assert settings.turn_silence_timeout_s == FAST_S
    assert module.RECONNECT_DELAY_S == module_before
    assert settings.turn_silence_timeout_s == settings_before


def test_set_pins_a_knob_the_table_does_not_carry(fast_timings):
    """The escape hatch for a one-off, which is why the table stays small."""
    module = importlib.import_module("app.domain.user.login_security")
    before = module.LOGIN_FIRST_WAIT_SECONDS
    fast_timings.set(module, "LOGIN_FIRST_WAIT_SECONDS", 0.1)
    assert module.LOGIN_FIRST_WAIT_SECONDS == 0.1
    assert fast_timings.applied["LOGIN_FIRST_WAIT_SECONDS"] == 0.1
    assert before != 0.1, "换了默认值就该重新看这条测试还在测什么"
