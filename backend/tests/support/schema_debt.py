"""Schema debt the migration rules forbid for new code, frozen where it stands.

``.claude/rules/migrations.md`` asks every new foreign key to be named and
indexed, every enum to carry a CHECK, every JSON column to be JSONB, and the
models to match what the migrations build. The schema already breaks each of
those in places; ``schema_debt_baseline.json`` lists every place by name. Two
ratchets hold it there:

- ``tests/unit/test_schema_debt.py`` reads the model metadata;
- ``tests/integration/test_schema_drift.py`` compares it with a database
  migrated to head (what ``alembic check`` reports).

A new entry fails: fix it instead of adding it. A paid-off entry fails too
until it is deleted from the baseline, so the file only ever shrinks.

Both measure in a fresh interpreter that imports ``app.models`` and nothing
else, exactly as ``alembic/env.py`` does. Inside a pytest session other test
files have already imported model modules ``app.models`` does not (on
2026-10-07: invite, ratchet, agent turn inputs), so ``Base.metadata`` there
depends on collection order.

    python -m tests.support.schema_debt metadata
    python -m tests.support.schema_debt drift <async database url>
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

BASELINE_PATH = Path(__file__).with_name("schema_debt_baseline.json")
_BACKEND = Path(__file__).resolve().parents[2]

_DIALECT = postgresql.dialect()


def load_baseline() -> dict[str, list[str]]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _leading_indexed(table: sa.Table) -> set[str]:
    """Columns some index, unique constraint or primary key starts with."""
    out: set[str] = set()
    if table.primary_key.columns:
        out.add(next(iter(table.primary_key.columns)).name)
    for index in table.indexes:
        columns = list(index.columns)
        if columns:
            out.add(columns[0].name)
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint) and list(constraint.columns):
            out.add(next(iter(constraint.columns)).name)
    out.update(c.name for c in table.columns if c.index or c.unique)
    return out


def metadata_debt(metadata: sa.MetaData) -> dict[str, set[str]]:
    """The four kinds of debt readable off the models, as ``table.column`` names."""
    debt: dict[str, set[str]] = {
        "anonymous_foreign_keys": set(),
        "enums_without_check": set(),
        "foreign_keys_without_index": set(),
        "json_not_jsonb": set(),
    }
    for table in metadata.sorted_tables:
        indexed = _leading_indexed(table)
        for fk in table.foreign_key_constraints:
            columns = [c.name for c in fk.columns]
            if fk.name is None:
                debt["anonymous_foreign_keys"].add(f"{table.name}.{','.join(columns)}")
            if columns[0] not in indexed:
                debt["foreign_keys_without_index"].add(f"{table.name}.{columns[0]}")
        for column in table.columns:
            kind = column.type
            if (
                isinstance(kind, sa.Enum)
                and not kind.native_enum
                and not kind.create_constraint
            ):
                debt["enums_without_check"].add(f"{table.name}.{column.name}")
            impl = kind.dialect_impl(_DIALECT)
            if isinstance(impl, sa.JSON) and not isinstance(impl, postgresql.JSONB):
                debt["json_not_jsonb"].add(f"{table.name}.{column.name}")
    return debt


def drift_key(diff: object) -> str:
    """One ``compare_metadata`` entry as a stable line: ``kind table.thing``."""
    if isinstance(diff, list):  # modify_* come grouped per column
        kind, _schema, table, column = diff[0][:4]
        return f"{kind} {table}.{column}"
    kind, *rest = diff  # type: ignore[misc]
    target = rest[-1] if kind in {"add_column", "remove_column"} else rest[0]
    if kind in {"add_column", "remove_column"}:
        return f"{kind} {rest[1]}.{target.name}"
    if isinstance(target, sa.Table):
        return f"{kind} {target.name}"
    if isinstance(target, sa.ForeignKeyConstraint):
        columns = ",".join(c.name for c in target.columns)
        return f"{kind} {target.parent.name}.{columns}->{target.referred_table.name}"
    table = getattr(target, "table", None)
    name = getattr(target, "name", None) or "<unnamed>"
    return f"{kind} {table.name if table is not None else '?'}.{name}"


def ratchet_failures(kind: str, found: set[str], baseline: set[str]) -> list[str]:
    """What a ratchet reports: new debt, and paid-off entries still listed."""
    failures: list[str] = []
    for entry in sorted(found - baseline):
        failures.append(
            f"new {kind}: {entry} — fix it (.claude/rules/migrations.md);"
            " do not add it to the baseline"
        )
    for entry in sorted(baseline - found):
        failures.append(
            f"paid off {kind}: {entry} — delete it from {BASELINE_PATH.name}"
        )
    return failures


def measure(*args: str) -> dict[str, list[str]]:
    """Run this module's ``__main__`` in a fresh interpreter (see the docstring)."""
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.schema_debt", *args],
        cwd=_BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.splitlines()[-1])


async def _drift(url: str) -> list[str]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.db import Base

    def compare(sync) -> list:
        # Tables an extension owns (PostGIS's spatial_ref_sys on some servers)
        # belong to the server, not to our migrations.
        owned = set(
            sync.execute(
                text(
                    "SELECT c.relname FROM pg_class c"
                    " JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'e'"
                    " WHERE c.relkind IN ('r', 'v', 'm', 'p')"
                )
            ).scalars()
        )

        def ours(name, kind, _parent) -> bool:
            return not (kind == "table" and name in owned)

        context = MigrationContext.configure(sync, opts={"include_name": ours})
        return compare_metadata(context, Base.metadata)

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            diffs = await connection.run_sync(compare)
    finally:
        await engine.dispose()
    return sorted(drift_key(diff) for diff in diffs)


def _main(argv: list[str]) -> dict[str, list[str]]:
    import logging

    logging.disable(logging.INFO)
    import app.models  # noqa: F401  (what alembic/env.py registers, and only that)
    from app.core.db import Base

    if argv[0] == "metadata":
        return {
            kind: sorted(entries)
            for kind, entries in metadata_debt(Base.metadata).items()
        }
    return {"alembic_check_drift": asyncio.run(_drift(argv[1]))}


if __name__ == "__main__":
    print(json.dumps(_main(sys.argv[1:])))
