"""A course's module switches are data, and absent means on.

These pin the two properties the 配置页 leans on: a stored map holds only the
exceptions (so `{}` is "everything shown", and a module added later needs no
backfill), and an unknown key never becomes a phantom switch on the read path.
"""

import pytest

from app.domain.space.course_modules import MODULE_KEYS, is_on, normalize


class TestNormalize:
    def test_absent_means_nothing_is_stored(self):
        assert normalize(None) == {}
        assert normalize({}) == {}

    def test_only_the_declared_keys_survive(self):
        assert normalize({"quiz": False, "nonsense": True}) == {"quiz": False}

    def test_false_is_the_point(self):
        assert normalize({"quiz": False}) == {"quiz": False}

    def test_the_order_follows_the_catalogue_not_the_input(self):
        # The 配置页 renders in catalogue order, so a stored map that came back
        # in another order must not make the rows jump around.
        stored = normalize({"team": False, "units": False})
        assert list(stored) == ["units", "team"]

    def test_a_truthy_non_boolean_is_coerced_not_kept(self):
        assert normalize({"quiz": 0}) == {"quiz": False}

    def test_a_non_mapping_is_emptied_rather_than_raising(self):
        # The read path runs on every page that renders a 题目版: one bad byte in
        # a board's JSON must not take the page down.
        assert normalize(["quiz"]) == {}  # type: ignore[arg-type]


class TestIsOn:
    def test_nothing_declared_means_everything_is_on(self):
        assert all(is_on({}, key) for key in MODULE_KEYS)

    def test_a_declared_false_is_off_and_the_rest_stay_on(self):
        declared = {"quiz": False}
        assert is_on(declared, "quiz") is False
        assert is_on(declared, "units") is True

    def test_an_unknown_module_is_a_programming_error(self):
        # Unlike a bad stored byte, asking about a module that does not exist is
        # a mistake in code and should be loud.
        with pytest.raises(ValueError):
            is_on({}, "not_a_module")
