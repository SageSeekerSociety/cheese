"""FRONTEND_ALIASES are browser origins, written as browsers send them.

A request's origin is found by comparing its Host with these, and a grant's or
a passkey's Origin header with them, so an alias written any other way would
never match and the origin it names would quietly fall back to the canonical
one.
"""

import pytest

from app.core.config import Settings


def _build(*aliases: str) -> Settings:
    return Settings(
        _env_file=None,
        frontend_url="https://example.com",
        frontend_aliases=list(aliases),
    )


def test_an_alias_is_written_the_way_browsers_send_it() -> None:
    assert _build(
        "https://Example.CN/", "https://other.example:443"
    ).frontend_aliases == [
        "https://example.cn",
        "https://other.example",
    ]


def test_the_canonical_origin_and_repeats_are_not_aliases() -> None:
    assert _build(
        "https://example.com", "https://example.cn", "https://example.cn"
    ).frontend_aliases == ["https://example.cn"]


@pytest.mark.parametrize(
    "value",
    [
        "http://example.cn",
        "example.cn",
        "https://example.cn/app",
        "https://example.cn?x=1",
        "https://user@example.cn",
    ],
)
def test_an_alias_that_is_not_an_https_origin_refuses_to_boot(value: str) -> None:
    with pytest.raises(RuntimeError, match="FRONTEND_ALIASES"):
        _build(value)


def test_the_docs_host_is_none_of_the_platforms_origins() -> None:
    with pytest.raises(RuntimeError, match="DOCS_ORIGIN"):
        Settings(
            _env_file=None,
            frontend_url="https://example.com",
            frontend_aliases=["https://example.cn"],
            docs_origin="https://example.cn",
        )
