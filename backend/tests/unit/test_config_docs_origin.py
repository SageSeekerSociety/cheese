"""DOCS_ORIGIN is the docs' own host, as browsers write it, and only that.

Each refused value used to boot and then fail where nobody looks: the
platform's own host made the docs server answer every platform request; an
explicit default port never matched the Origin browsers send, so every question
was refused; a blank value meant "unset" here and stopped the frontend there.
"""

import pytest

from app.core.config import Settings


def _build(**overrides: object) -> Settings:
    return Settings(_env_file=None, frontend_url="https://okcheese.com", **overrides)


@pytest.mark.parametrize(
    "value",
    ["https://okcheese.com", "https://OKCHEESE.com/", "https://okcheese.com:443"],
)
def test_the_platforms_own_host_is_not_a_docs_host(value: str) -> None:
    with pytest.raises(RuntimeError, match="DOCS_ORIGIN"):
        _build(docs_origin=value)


def test_a_default_port_is_written_the_way_browsers_send_it() -> None:
    assert _build(docs_origin="https://docs.okcheese.com:443").docs_origin == (
        "https://docs.okcheese.com"
    )
    assert (
        Settings(
            _env_file=None,
            frontend_url="http://app.localhost:5200",
            docs_origin="http://docs.localhost:80",
        ).docs_origin
        == "http://docs.localhost"
    )
    # A port that is not the default stays: it is part of the origin.
    assert _build(docs_origin="https://docs.okcheese.com:8443").docs_origin == (
        "https://docs.okcheese.com:8443"
    )


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_is_unset(value: str) -> None:
    assert _build(docs_origin=value).docs_origin == ""


@pytest.mark.parametrize(
    "value",
    ["docs.okcheese.com", "https://docs.okcheese.com/docs", "http://docs.okcheese.com"],
)
def test_anything_but_an_origin_is_refused(value: str) -> None:
    with pytest.raises(RuntimeError, match="DOCS_ORIGIN"):
        _build(docs_origin=value)
