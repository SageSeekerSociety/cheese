"""Print the schema this release's pending migrations drop, rename or alter.

deploy-docker.sh feeds this file on stdin to `python -` inside the release
image before it migrates, and compares the answer with what each standing
connection owner's models map (deploy/schema-map.py). An owner is released
across the migrations only when they touch something it maps: a table they
drop or rename, or a column they drop, rename or alter. Adding a table or a
column is not a touch: a model never selects what it does not map, and
.claude/rules/migrations.md requires an added column to be nullable or to
have a server default, so the owner's inserts still succeed.

Output, one item per line:
  migration-touches pending <revision>   a migration this deploy will run
  migration-touches <table>              a table it drops, renames or changes
                                         an index or constraint of
  migration-touches <table>.<column>     a column it drops, renames or alters
  migration-touches *                    something it changes that this cannot read
  migration-touches present <table>[.<column>]  what the database has now
  migration-touches ok                   the answer is complete

`*` touches everything: a name that is not a literal, SQL that comes from
outside the file, a schema statement this does not parse, or code from another
migration is not proof that the change is safe. Only `upgrade()` and the
module-level code it can call are read; a deploy never runs `downgrade()`.

The `present` lines let the deploy release an owner that already maps
something the database lacks: migrations commit one by one, so a deploy whose
second migration failed leaves the first one's drop in place, and the next
deploy's pending list no longer shows it.

`python - --files A.py B.py` reads the named migration files instead of asking
the database which ones are pending (the deploy tests use it).
"""

from __future__ import annotations

import ast
import itertools
import re
import sys

UNKNOWN = "*"
HOLE = "\x00"

_DROP_TABLE = re.compile(r"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w\",.\s\x00]+?)(?:\s+CASCADE|\s+RESTRICT|\s*$)", re.I)
_ALTER_TABLE = re.compile(r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:\"?[\w\x00]+\"?\.)?\"?([\w\x00]+)\"?(.*)$", re.I | re.S)
_RENAME_TABLE = re.compile(r"^\s*RENAME\s+TO\b", re.I)
_COLUMN_CLAUSE = re.compile(
    r"^\s*(?:RENAME|DROP|ALTER)\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?\"?([\w\x00]+)\"?", re.I
)
# Clauses that leave every column as an older model reads and writes it:
# constraints, indexes, defaults, triggers, storage. DROP NOT NULL only widens
# what a column accepts.
_HARMLESS = re.compile(
    r"^\s*(?:ADD\b|VALIDATE\b|DROP\s+(?:CONSTRAINT|TRIGGER)\b|ENABLE\b|DISABLE\b|SET\s*\(|RESET\b|OWNER\b|CLUSTER\b|"
    r"ALTER\s+(?:COLUMN\s+)?\"?\w+\"?\s+(?:SET\s+DEFAULT|DROP\s+DEFAULT|DROP\s+NOT\s+NULL|SET\s+STATISTICS|SET\s+STORAGE)\b)",
    re.I,
)
# A statement that starts like this changes rows or adds things, never what an
# older model reads; anything else that drops, renames, alters or runs SQL it
# builds itself is read below or counts as touching everything.
_ADDITIVE = re.compile(
    r"^\s*(?:CREATE|COMMENT|ANALYZE|SELECT|INSERT|UPDATE|DELETE|WITH|SET|RESET|LOCK|GRANT|REVOKE|"
    r"REFRESH|NOTIFY|VACUUM|REINDEX|CLUSTER)\b",
    re.I,
)
_SCHEMA_CHANGE = re.compile(r"\b(?:DROP|RENAME|ALTER|EXECUTE|TRUNCATE)\b", re.I)
# Calls that run another migration's code, which this file does not show.
_FOREIGN_CODE = {"upgrade", "exec_module", "import_module", "run_path", "exec"}
# SQLAlchemy DDL elements an execute may be handed: these leave columns alone.
_HARMLESS_ELEMENTS = {"CreateSequence", "DropSequence", "CreateIndex", "AddConstraint"}
_ADD_NOT_NULL = re.compile(
    r"^\s*ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\"?\w+\"?\s+.*\bNOT\s+NULL\b", re.I | re.S
)
# Core statements built from a table object (update(), insert(), …) change rows.
_ROW_STATEMENTS = {"update", "insert", "delete", "select", "values", "where"}


def _callee(node: ast.AST) -> str | None:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _clauses(rest: str) -> list[str]:
    """``a, b(c, d), e`` → ``[a, b(c, d), e]``: commas outside parentheses."""
    clauses, depth, start = [], 0, 0
    for i, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            clauses.append(rest[start:i])
            start = i + 1
    clauses.append(rest[start:])
    return clauses


class Migration:
    """What one migration file touches."""

    def __init__(self, source: str) -> None:
        self.touches: set[str] = set()
        tree = ast.parse(source)
        self.constants: dict[str, ast.AST] = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                self.constants[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                self.constants[stmt.target.id] = stmt.value
        nodes = [
            node
            for top in tree.body
            if not (isinstance(top, ast.FunctionDef) and top.name == "downgrade")
            for node in ast.walk(top)
        ]
        # Loop variables resolve through the loop that binds them.
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        # `with op.batch_alter_table("t") as batch:` names the table for batch.*.
        self.batches: dict[str, ast.AST | None] = {}
        for node in nodes:
            if isinstance(node, ast.With):
                for item in node.items:
                    if _callee(item.context_expr) == "batch_alter_table" and isinstance(item.optional_vars, ast.Name):
                        args = item.context_expr.args  # type: ignore[attr-defined]
                        self.batches[item.optional_vars.id] = args[0] if args else None
        for node in nodes:
            if isinstance(node, ast.Call):
                self._call(node)

    # ---- values ----

    def _rows(self, node: ast.AST) -> list[ast.AST] | None:
        if isinstance(node, ast.Name) and node.id in self.constants:
            return self._rows(self.constants[node.id])
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return list(node.elts)
        return None

    def _binding(self, name: ast.Name) -> tuple[ast.AST, int | None] | None:
        """The nearest loop binding a name, and its place in a tuple target."""
        node: ast.AST | None = name
        while node is not None:
            for loop in [node] if isinstance(node, ast.For) else getattr(node, "generators", []):
                if isinstance(loop.target, ast.Name) and loop.target.id == name.id:
                    return loop, None
                for position, element in enumerate(getattr(loop.target, "elts", [])):
                    if isinstance(element, ast.Name) and element.id == name.id:
                        return loop, position
            node = self.parents.get(node)
        return None

    def _row_values(self, loop: ast.AST, position: int | None) -> list[list[str] | None] | None:
        """What a loop variable takes on each pass, or None if not literals."""
        rows = self._rows(loop.iter)  # type: ignore[attr-defined]
        if rows is None:
            return None
        if position is None:
            return [self.values(row) for row in rows]
        out = []
        for row in rows:
            fields = self._rows(row)
            out.append(self.values(fields[position]) if fields and position < len(fields) else None)
        return out

    def _loop(self, name: ast.Name) -> list[str] | None:
        """Every value a loop variable takes, from the nearest loop binding it."""
        binding = self._binding(name)
        if binding is None:
            return None
        passes = self._row_values(*binding)
        if passes is None or any(found is None for found in passes):
            return None
        return [value for found in passes for value in found]  # type: ignore[union-attr]

    def _pairs(self, table: ast.AST | None, column: ast.AST | None) -> list[tuple[str, str]]:
        """(table, column) an op call names; a loop over (table, column) rows
        pairs them row by row rather than every table with every column."""
        if isinstance(table, ast.Name) and isinstance(column, ast.Name):
            tb, cb = self._binding(table), self._binding(column)
            if tb and cb and tb[0] is cb[0] and tb[1] is not None and cb[1] is not None:
                tables, columns = self._row_values(*tb), self._row_values(*cb)
                if tables is not None and columns is not None:
                    pairs = []
                    for t, c in zip(tables, columns):
                        for one in itertools.product(t or [UNKNOWN], c or [UNKNOWN]):
                            pairs.append(one)
                    return pairs
        return list(itertools.product(self._names(table), self._names(column)))

    def values(self, node: ast.AST | None) -> list[str] | None:
        """Every string the expression can be; HOLE marks what cannot be read."""
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    parts.append(self.values(value.value) or [HOLE])
                else:
                    parts.append(self.values(value) or [HOLE])
            return ["".join(combo) for combo in itertools.product(*parts)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # An unreadable operand is a hole in the text, not the whole of it.
            left, right = self.values(node.left) or [HOLE], self.values(node.right) or [HOLE]
            return [a + b for a, b in itertools.product(left, right)]
        if isinstance(node, ast.Call):
            if _callee(node) == "text" and node.args:
                return self.values(node.args[0])
            # sa.text("…").bindparams(…), "…".format(…): the SQL is the receiver.
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"bindparams", "columns", "format"}:
                return self.values(node.func.value)
        if isinstance(node, ast.Name):
            looped = self._loop(node)
            if looped is not None:
                return looped
            if node.id in self.constants:
                return self.values(self.constants[node.id])
        return None

    def _names(self, node: ast.AST | None) -> list[str]:
        found = self.values(node)
        if not found:
            return [UNKNOWN]
        return [UNKNOWN if HOLE in name else name for name in found]

    # ---- calls ----

    def _table(self, node: ast.Call) -> ast.AST | None:
        """The table expression an op call names."""
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in self.batches:
            return self.batches[func.value.id]
        for keyword in node.keywords:
            if keyword.arg in {"table_name", "old_table_name"}:
                return keyword.value
        return node.args[0] if node.args else None

    def _table_and_column(self, node: ast.Call) -> tuple[ast.AST | None, ast.AST | None]:
        func = node.func
        batch = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in self.batches
        named = any(keyword.arg == "table_name" for keyword in node.keywords)
        rest = list(node.args) if batch or named else list(node.args[1:])
        column = rest[0] if rest else None
        for keyword in node.keywords:
            if keyword.arg in {"column_name", "column"}:
                column = keyword.value
        return self._table(node), column

    def _call(self, node: ast.Call) -> None:
        name = _callee(node)
        if name in _FOREIGN_CODE:
            self.touches.add(UNKNOWN)
        elif name in {"drop_table", "rename_table"}:
            self.touches.update(self._names(self._table(node)))
        elif name in {"drop_index", "drop_constraint"}:
            # A unique index or constraint is what an older writer's
            # ON CONFLICT names; without it every such write fails.
            table = None
            for keyword in node.keywords:
                if keyword.arg == "table_name":
                    table = keyword.value
            if table is None and name == "drop_constraint" and len(node.args) > 1:
                table = node.args[1]
            self.touches.update(self._names(table))
        elif name == "add_column":
            # NOT NULL without a server default: an older writer's insert fails.
            column = node.args[1] if len(node.args) > 1 else None
            if isinstance(column, ast.Call) and _callee(column) == "Column":
                keywords = {keyword.arg: keyword.value for keyword in column.keywords}
                nullable = keywords.get("nullable")
                if (
                    isinstance(nullable, ast.Constant)
                    and nullable.value is False
                    and "server_default" not in keywords
                ):
                    self.touches.update(self._names(self._table(node)))
        elif name in {"drop_column", "alter_column"}:
            table, column = self._table_and_column(node)
            for one_table, one_column in self._pairs(table, column):
                self.touches.add(UNKNOWN if UNKNOWN in (one_table, one_column) else f"{one_table}.{one_column}")
        elif name in {"execute", "exec_driver_sql"}:
            self._execute(node.args[0] if node.args else None)

    def _execute(self, argument: ast.AST | None) -> None:
        if isinstance(argument, ast.Call) and _callee(argument) in _HARMLESS_ELEMENTS:
            return
        chain = argument
        while isinstance(chain, ast.Call) and isinstance(chain.func, ast.Attribute):
            if chain.func.attr in _ROW_STATEMENTS:
                return
            chain = chain.func.value
        statements = self.values(argument)
        if statements is None:
            self.touches.add(UNKNOWN)
            return
        for sql in statements:
            for statement in re.sub(r"--[^\n]*", "", sql).split(";"):
                self._statement(statement)

    def _statement(self, sql: str) -> None:
        sql = sql.strip()
        if not sql:
            return
        if sql.startswith(HOLE):
            # The statement itself is a hole: it could be anything.
            self.touches.add(UNKNOWN)
            return
        if _ADDITIVE.match(sql):
            return
        if match := _DROP_TABLE.match(sql):
            for name in re.split(r"[\s,]+", match.group(1).replace('"', "")):
                name = name.rsplit(".", 1)[-1]
                if name:
                    self.touches.add(UNKNOWN if HOLE in name else name)
            return
        match = _ALTER_TABLE.match(sql)
        if not match:
            if _SCHEMA_CHANGE.search(sql) or HOLE in sql:
                # ALTER TYPE, DROP VIEW, a DO block, dynamic EXECUTE, …
                self.touches.add(UNKNOWN)
            return
        table, rest = match.group(1), match.group(2)
        if HOLE in table:
            self.touches.add(UNKNOWN)
            return
        for clause in _clauses(rest):
            if _ADD_NOT_NULL.match(clause) and not re.search(r"\bDEFAULT\b", clause, re.I):
                # An older writer's insert leaves it empty and fails.
                self.touches.add(table)
                continue
            if not clause.strip() or _HARMLESS.match(clause):
                continue
            if HOLE in clause.split()[0]:
                # The clause itself is a hole: it could be anything.
                self.touches.add(UNKNOWN)
            elif _RENAME_TABLE.match(clause):
                self.touches.add(table)
            elif column := _COLUMN_CLAUSE.match(clause):
                self.touches.add(UNKNOWN if HOLE in column.group(1) else f"{table}.{column.group(1)}")
            else:
                # A clause this does not recognise may change what the owner reads.
                self.touches.add(table)


def read_database(database_url: str, config: str = "alembic.ini") -> tuple[list[tuple[str, str]], list[str]]:
    """(revision, path) of every migration `alembic upgrade head` would run on
    the database, and every table and table.column it has now."""
    import asyncio

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    async def read() -> tuple[tuple[str, ...], list[str]]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                heads = await connection.run_sync(
                    lambda sync: tuple(MigrationContext.configure(sync).get_current_heads())
                )
                rows = await connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                    )
                )
                present = set()
                for table, column in rows:
                    present.update((table, f"{table}.{column}"))
                return heads, sorted(present)
        finally:
            await engine.dispose()

    script = ScriptDirectory.from_config(Config(config))
    heads, present = asyncio.run(read())
    pending = [(rev.revision, rev.path) for rev in script.iterate_revisions("heads", heads or "base")]
    return pending, present


def main(argv: list[str]) -> None:
    present: list[str] = []
    if argv[:1] == ["--files"]:
        migrations = [(path, path) for path in argv[1:]]
    else:
        from app.core.config import settings

        migrations, present = read_database(settings.database_url)
    for item in present:
        print(f"migration-touches present {item}")
    for revision, path in migrations:
        print(f"migration-touches pending {revision}")
        with open(path, encoding="utf-8") as handle:
            for touched in sorted(Migration(handle.read()).touches):
                print(f"migration-touches {touched}")
    print("migration-touches ok")


if __name__ == "__main__":
    main(sys.argv[1:])
