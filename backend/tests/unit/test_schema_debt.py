"""The schema debt readable off the models only shrinks.

See tests/support/schema_debt.py.
"""

import pytest

from tests.support.schema_debt import load_baseline, measure, ratchet_failures

KINDS = (
    "anonymous_foreign_keys",
    "enums_without_check",
    "foreign_keys_without_index",
    "json_not_jsonb",
)


@pytest.fixture(scope="module")
def debt() -> dict[str, list[str]]:
    return measure("metadata")


@pytest.mark.parametrize("kind", KINDS)
def test_schema_debt_only_shrinks(debt: dict[str, list[str]], kind: str) -> None:
    failures = ratchet_failures(kind, set(debt[kind]), set(load_baseline()[kind]))
    assert not failures, "\n".join(failures)
