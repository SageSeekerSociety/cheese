"""One copy of each result column's description, shared by every statement.

SQLAlchemy keeps, beside each statement a pooled connection has prepared, the
columns asyncpg described for it (`PreparedStatement.get_attributes()`), to
build each execution's `cursor.description`. asyncpg builds a new
`Attribute(name, Type(oid, name, kind, schema))` pair per column on every
call, and both are tuple subclasses, which the garbage collector never stops
tracking (it untracks only exact tuples). With 300 statements on each pooled
connection (`DB_STATEMENT_CACHE_SIZE`) they were 77,000 of the 353,000
objects production's census (`core/heap_census`) found, walked by every full
collection, with the event loop held.

The descriptions are immutable and repeat endlessly (`id`, `created_at`,
`project_id` under a handful of types), so each distinct one is kept once and
every statement's tuple points at it.
"""

from asyncpg import prepared_stmt
from asyncpg.types import Attribute, Type

_types: dict[Type, Type] = {}
_attributes: dict[tuple[str, Type], Attribute] = {}
_described = prepared_stmt.PreparedStatement.get_attributes


def _shared(attribute: Attribute) -> Attribute:
    kind = _types.setdefault(attribute.type, attribute.type)
    key = (attribute.name, kind)
    found = _attributes.get(key)
    if found is None:
        found = _attributes[key] = Attribute(attribute.name, kind)
    return found


def get_attributes(self) -> tuple[Attribute, ...]:
    """`PreparedStatement.get_attributes`, with each column's description
    the one shared copy of it."""
    return tuple(_shared(attribute) for attribute in _described(self))


def install() -> None:
    """Make every prepared statement describe its columns with the shared
    copies. Idempotent."""
    prepared_stmt.PreparedStatement.get_attributes = get_attributes
