"""BM25 indexes for project context search

Revision ID: 2d2fc3a8ce36
Revises: 19fe29bcb352

`GET /projects/{id}/context/search` matched `ILIKE '%q%'` and listed hits newest
first. It now matches word by word and ranks by relevance (the query side is
`app.domain.search.bm25`), which needs a BM25 index on each table it searches:

| table | text (jieba + `_ngram` copy) | filter columns |
|---|---|---|
| `blocks` | `content` | `topic_id`, `kind` |
| `topics` | `title` | `project_id` |
| `tasks` | `title`, `brief`, `conclusion` | `room_id` |
| `project_artifacts` | `name`, `about` | `project_id` |

The filter columns use the keyword tokenizer so the readable-room and project
restrictions run inside the search; outside it `paradedb.score()` is NULL. The
`pg_search` extension comes from 19fe29bcb352.

## `blocks` is partial, and what a write costs

`blocks` takes a row for every message and every step of a turn. The index is
partial on the kinds the search reads (`SEARCHED_KINDS`, which must equal
`SEARCHED_BLOCKS` in the route): `event` blocks, which a running turn keeps
rewriting (`record_step_output`, `restate`), stay out of it.

Measured on `paradedb/paradedb:v0.18.8-pg16`, a 20000-row copy of `blocks`
with its btree indexes (random Chinese text, ~110 characters), one statement
per transaction, 1000 transactions each:

| statement | no BM25 index | with this index |
|---|---|---|
| insert a message | 0.16 ms | 1.05 ms |
| rewrite a message's content | 0.18 ms | 1.15 ms |
| rewrite a message's `meta` only | 0.19 ms | 1.08 ms |
| rewrite an `event` (outside the predicate) | 0.21 ms | 0.23 ms |

The added ~0.9 ms is paid per commit, not per row: 1000 inserts in one
transaction took 236 ms (0.24 ms a row). A `meta`-only update pays it too
because no update of `blocks.meta` is HOT (`ix_blocks_cloud_provisioning`'s
predicate reads `meta`), so each one inserts into every index. Messages are
written whole, not streamed, so a turn adds that cost once per message plus
once per turn-accounting stamp on the messages it read.

Build: 1.1 s per 20000 rows; the index is about 65% of the heap's size. The
first jieba query in a new connection costs ~150 ms (the dictionary loads once
per backend).

## Downgrade

Drops the four indexes; the extension stays with 19fe29bcb352.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d2fc3a8ce36"
down_revision: str | Sequence[str] | None = "19fe29bcb352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The block kinds the project search reads; equal to the route's
#: `SEARCHED_BLOCKS`. A kind missing here is never found; one extra only costs
#: writes.
SEARCHED_KINDS = ("message", "doc", "doc_node", "comment", "decision", "weekly")


def _text_fields(texts: Sequence[str], keywords: Sequence[str]) -> str:
    # A space after every ':' — `op.execute` wraps the string in `text()`,
    # which reads `:word` as a bind parameter.
    fields = []
    for column in texts:
        fields.append(
            f'"{column}": {{"tokenizer": {{"type": "jieba"}}, "record": "position"}}'
        )
        fields.append(
            f'"{column}_ngram": {{"column": "{column}", "tokenizer": '
            '{"type": "ngram", "min_gram": 2, "max_gram": 3, "prefix_only": false}}'
        )
    for column in keywords:
        fields.append(f'"{column}": {{"tokenizer": {{"type": "keyword"}}}}')
    return "{" + ", ".join(fields) + "}"


#: (index, table, text columns, filter columns, partial predicate)
_INDEXES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        "ix_blocks_search",
        "blocks",
        ("content",),
        ("topic_id", "kind"),
        "kind IN (" + ", ".join(f"'{k}'" for k in SEARCHED_KINDS) + ")",
    ),
    ("ix_topics_search", "topics", ("title",), ("project_id",), ""),
    ("ix_tasks_search", "tasks", ("title", "brief", "conclusion"), ("room_id",), ""),
    (
        "ix_project_artifacts_search",
        "project_artifacts",
        ("name", "about"),
        ("project_id",),
        "",
    ),
)


def upgrade() -> None:
    for name, table, texts, keywords, where in _INDEXES:
        columns = ", ".join(("id", *texts, *keywords))
        op.execute(
            f"CREATE INDEX {name} ON {table} USING bm25 ({columns}) "
            f"WITH (key_field = 'id', text_fields = '{_text_fields(texts, keywords)}')"
            + (f" WHERE {where}" if where else "")
        )


def downgrade() -> None:
    for name, *_ in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
