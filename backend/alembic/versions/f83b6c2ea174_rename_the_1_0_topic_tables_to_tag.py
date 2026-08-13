"""rename the 1.0 topic tables to tag

#370: `topic` named two things. Here it is a label a question, a 赛题 or a Space
is filed under — id, name, creator. In cheesex it is a room with a roster, a
document, a branch and 芝士. They were separated by singular vs plural, and
losing one `/api` layer sent `/api/topics` to the 1.0 router, which answered 200
with tags (docs/api-conventions.md records the outage).

Renames only — rows, keys and foreign keys survive, and it reverses cleanly.

What moves: the four tables, the `topic_id` column on each relation table, the
one sequence the model names explicitly (`task_topics_relation_seq` — a model
that named it while the database called it something else would break every
insert), and the three indexes whose own names carry the word.

What deliberately does not: PostgreSQL's auto-generated names (`topic_pkey`,
`task_topics_relation_task_id_fkey`, the serial sequences). Nothing in the code
names them, renaming them is pure churn, and each one is another statement that
can fail on a database whose history differs slightly from ours.

Also unchanged, and NOT an oversight: the JSON keys (`topics`, `classificationTopics`)
and the Meilisearch index called `topics`. The first is product language the 知是
UI shows to humans; the second is an external system's namespace holding live
data, so moving it is a reindex, not a migration.

Revision ID: f83b6c2ea174
Revises: e7c2b41d90a5
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f83b6c2ea174"
down_revision: str | Sequence[str] | None = "e7c2b41d90a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = [
    ("topic", "tag"),
    ("question_topic_relation", "question_tag_relation"),
    ("task_topics_relation", "task_tag_relation"),
    ("space_classification_topics_relation", "space_classification_tag_relation"),
]

# (table AFTER the rename above, old column, new column)
_COLUMNS = [
    ("question_tag_relation", "topic_id", "tag_id"),
    ("task_tag_relation", "topic_id", "tag_id"),
    ("space_classification_tag_relation", "topic_id", "tag_id"),
]

_INDEXES = [
    ("ix_topic_fts", "ix_tag_fts"),
    ("ix_space_classification_topics_space", "ix_space_classification_tag_space"),
    ("ix_space_classification_topics_topic", "ix_space_classification_tag_tag"),
]

_SEQUENCES = [("task_topics_relation_seq", "task_tags_relation_seq")]


def _rename(pairs: list[tuple[str, str]], kind: str) -> None:
    # IF EXISTS so a database built from a different point in this chain's
    # history is skipped rather than aborting halfway. A half-applied rename is
    # the one outcome worth engineering away.
    for old, new in pairs:
        op.execute(f'ALTER {kind} IF EXISTS "{old}" RENAME TO "{new}"')


def _rename_columns(triples: list[tuple[str, str, str]]) -> None:
    for table, old, new in triples:
        op.execute(f'ALTER TABLE IF EXISTS "{table}" RENAME COLUMN "{old}" TO "{new}"')


def upgrade() -> None:
    _rename(_TABLES, "TABLE")
    _rename_columns(_COLUMNS)
    _rename(_INDEXES, "INDEX")
    _rename(_SEQUENCES, "SEQUENCE")


def downgrade() -> None:
    _rename([(new, old) for old, new in _SEQUENCES], "SEQUENCE")
    _rename([(new, old) for old, new in _INDEXES], "INDEX")
    _rename_columns([(table, new, old) for table, old, new in _COLUMNS])
    _rename([(new, old) for old, new in _TABLES], "TABLE")
