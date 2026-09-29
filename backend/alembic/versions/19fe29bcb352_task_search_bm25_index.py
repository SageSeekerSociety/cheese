"""BM25 index for space task search

Revision ID: 19fe29bcb352
Revises: 7b2d4e9c1a60

Task search matched `ILIKE '%keywords%'` on name and intro: no ranking, and a
query of two words only hit rows containing them adjacent, separated by that
exact whitespace. This restores what the Java backend had: a ParadeDB
(`pg_search`) BM25 index, queried per word and ordered by relevance.

## Each text column is indexed twice

- `name` / `intro` with the `jieba` tokenizer: whole-word matches, which is
  what relevance should reward.
- `name_ngram` / `intro_ngram` with a 2..3-character ngram tokenizer: jieba
  segments 「季度财务报表」 into 季度 / 财务 / 报表 / 财务报表, so a query for
  「务报」 (the middle of a word) matches no jieba token. The ngram copy is what
  still finds it. The Java backend indexed these copies but never queried them.

A single character matches neither copy: jieba keeps it inside a longer word
and the ngrams start at two. `ILIKE` did find it.

## What the index has to carry besides text

`deleted_at` is in the column list although nothing searches it. The index is
partial on `deleted_at IS NULL`, and every query repeats that predicate; when
the column is not in the index, ParadeDB cannot evaluate that predicate itself,
falls back to a plain index scan, and `paradedb.score()` silently returns NULL
(measured on this image). `space_id` is there so the space filter is applied
inside the search rather than after it.

The remaining task filters (category, approval, lifecycle, visibility, joined)
are not indexed; the query scores inside a materialised CTE and applies them
outside it, because any predicate ParadeDB cannot push down turns the score
NULL just as above (see `TaskRepository._keyword_hits`).

## Extension and downgrade

The deployed image is `paradedb/paradedb:v0.18.8-pg16`, which ships
`pg_search` 0.18.8. The downgrade drops the index and keeps the extension, for
the reason given in `d4e7a91b3c58`.

## Lock

`CREATE INDEX` without `CONCURRENTLY` (alembic runs in a transaction) holds an
exclusive lock on `task` while it builds; the table is small.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "19fe29bcb352"
down_revision: str | Sequence[str] | None = "7b2d4e9c1a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every ':' in the JSON is followed by a space: `op.execute` wraps the string in
# `text()`, which reads `:word` as a bind parameter.
_TEXT_FIELDS = """{
    "name": {"tokenizer": {"type": "jieba"}, "record": "position"},
    "intro": {"tokenizer": {"type": "jieba"}, "record": "position"},
    "name_ngram": {"column": "name", "tokenizer":
        {"type": "ngram", "min_gram": 2, "max_gram": 3, "prefix_only": false}},
    "intro_ngram": {"column": "intro", "tokenizer":
        {"type": "ngram", "min_gram": 2, "max_gram": 3, "prefix_only": false}}
}"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    op.execute(
        "CREATE INDEX ix_task_search ON task "
        "USING bm25 (id, name, intro, space_id, deleted_at) "
        f"WITH (key_field = 'id', text_fields = '{_TEXT_FIELDS}') "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_task_search")
