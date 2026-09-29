"""Keyword queries against the ParadeDB (`pg_search`) BM25 indexes.

Every searched text column is indexed twice: once with the jieba tokenizer
(whole words, which relevance should reward) and once as ``<column>_ngram``
with 2..3-character ngrams (the middle of a word, which jieba cannot find).
This module builds the query that reads both.

Two things about `pg_search` 0.18.8 that the callers depend on:

- jieba emits whitespace as a token. A phrase sent whole would require, and
  match, the whitespace itself, so the words are split here and each must be
  found (in any of the fields) on its own.
- `paradedb.score()` is computed only when ParadeDB evaluates every predicate
  of the scan itself; it silently returns NULL otherwise. A caller that orders
  by score keeps its other predicates in the index (``filters``) or outside
  the scan (a CTE joined back).

`match` treats its value as literal text, not query syntax, so user input
needs no escaping.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import ColumnElement, literal
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import InstrumentedAttribute


def words(q: str | None) -> list[str]:
    """The words of ``q``; every one of them has to be found."""
    return (q or "").split()


def any_of(field: str, values: Iterable[uuid.UUID | str]) -> dict[str, Any]:
    """A filter: ``field`` (a keyword-tokenized column) is one of ``values``."""
    return {"term_set": {"terms": [{"field": field, "value": str(v)} for v in values]}}


def match_all_words(
    key: ColumnElement[Any] | InstrumentedAttribute[Any],
    terms: list[str],
    fields: Mapping[str, float],
    *,
    filters: Iterable[dict[str, Any]] = (),
) -> ColumnElement[bool]:
    """``key @@@ query``: every word in ``terms`` matches one of ``fields``.

    ``fields`` maps each text column to its weight; its ngram copy carries the
    same weight. ``filters`` restrict the result; they go under ``must``
    because 0.18.8 accepts a boolean query's ``filter`` clause and ignores it,
    returning rows outside the restriction.
    """

    def match(field: str, word: str, weight: float) -> dict[str, Any]:
        return {
            "boost": {
                "factor": weight,
                "query": {
                    "match": {"field": field, "value": word, "conjunction_mode": True}
                },
            }
        }

    query = {
        "boolean": {
            "must": [
                {
                    "boolean": {
                        "should": [
                            match(name, word, weight)
                            for column, weight in fields.items()
                            for name in (column, f"{column}_ngram")
                        ]
                    }
                }
                for word in terms
            ]
            + list(filters),
        }
    }
    return key.op("@@@")(literal(query, JSONB))
