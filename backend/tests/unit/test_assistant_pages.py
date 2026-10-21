"""The pages a person's 芝士 can be asked from: one table, read by the route.

The kinds a question may name are the table's keys, and the request schema
lists exactly those; where a conversation was started is a page only when it
is one of them; and what a question is billed as comes from the page.
"""

from typing import get_args

from app.domain.agent.personal import billing, pages


def test_the_request_names_exactly_the_pages_there_are_readers_for() -> None:
    assert set(get_args(pages.PageKind)) == set(pages.PAGES)


def test_a_conversation_defaults_to_the_page_it_was_started_on() -> None:
    assert pages.home_page("task", "7") == ("task", "7")
    assert pages.home_page("draft", "12") == ("draft", "12")
    # Only a 题目 or a draft was ever a place a conversation started on; an id
    # that is not one is no page.
    assert pages.home_page("project", "3") is None
    assert pages.home_page("task", "abc") is None


def test_writing_a_draft_is_billed_as_writing_and_the_rest_as_asking() -> None:
    assert pages.billed_as("draft") == billing.WRITING
    assert pages.billed_as("task") == billing.ASKING
    assert pages.billed_as(None) == billing.ASKING
