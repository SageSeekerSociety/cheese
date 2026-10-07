#!/usr/bin/env python3
"""Fail when a migration this branch adds would break the release still serving.

A deploy runs ``alembic upgrade head`` while the previous backend is still
serving, and only swaps containers once it succeeds
(``deploy/deploy-docker.sh``). Every migration must therefore leave a database
the previous release can still read and write, and must not hold a lock that
stalls it. ``.claude/rules/migrations.md`` has the rules and why; this script
checks the ones that can be read off the source:

  drop-column / drop-table / rename
      The previous release still maps the column or table (``origin/main``'s
      models are read to tell), so it fails the moment the migration commits.
      Ship the code that stops using it first; drop it in a later PR.
  index-not-concurrent / index-outside-autocommit
      ``CREATE INDEX`` on an existing table without ``CONCURRENTLY`` blocks
      writes to it for the whole build; ``CONCURRENTLY`` must run outside a
      transaction, i.e. in ``op.get_context().autocommit_block()``.
  constraint-not-valid
      A foreign key or CHECK added to an existing table without ``NOT VALID``
      scans it under lock. Add it ``NOT VALID`` and ``VALIDATE`` separately.
  unique-constraint
      ``ADD CONSTRAINT ... UNIQUE`` on an existing table builds its index under
      lock. Build a unique index ``CONCURRENTLY``, then ``ADD ... USING INDEX``.
  add-column-not-null
      A NOT NULL column without a server default cannot be added to a table
      that has rows, and the previous release would not fill it.
  fk-without-index
      A new foreign key column needs an index that starts with it, or deleting
      a referenced row scans the whole referencing table.
  json-not-jsonb
      New JSON columns are JSONB (comparable, indexable).
  app-import
      Migrations never import ``app.*``: they run long after they are written,
      against whatever ``app`` has become by then.
  unresolved-drop
      A drop or rename whose table or column is not a literal cannot be
      checked; spell the names out (or justify an exception).
  lock-retry-copy
      Lock retries come from ``migration_helpers.with_lock_retries``, not a
      pasted ``DO`` block.

Only migrations ADDED relative to the merge base with ``--base`` are judged:
the ones on main are immutable, and judging them would make every PR pay for
history. Statements in ``downgrade()`` are not judged.

An exception is written next to the statement it excuses, with a reason:

    op.drop_column("t", "c")  # migration-safety: allow drop-column — <why>

(on the statement's lines or the line above), or once for the whole file:

    # migration-safety: allow-file index-not-concurrent — <why>

Usage:
    check-migration-safety.py [--base origin/main] [--versions-dir DIR] [FILE ...]
    check-migration-safety.py --self-test

FILEs, when given, are judged instead of the added ones (still against
``--base``'s models).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSIONS_DIR = "backend/alembic/versions"
MODELS_DIR = "backend/app"

ERROR_RULES = frozenset(
    {
        "drop-column",
        "drop-table",
        "rename",
        "index-not-concurrent",
        "index-outside-autocommit",
        "constraint-not-valid",
        "unique-constraint",
        "add-column-not-null",
        "fk-without-index",
        "json-not-jsonb",
        "app-import",
        "lock-retry-copy",
        "unresolved-drop",
    }
)
WARNING_RULES = frozenset({"alter-column-existing", "fk-on-add-column", "unresolved"})

_PRAGMA = re.compile(
    r"#\s*migration-safety:\s*(allow|allow-file)\s+([a-z0-9,\- ]+?)\s*(?:—|--|:)\s*(\S.*)$"
)


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.rule in ERROR_RULES


# --------------------------------------------------------------------------
# The previous release's models: which tables and columns it maps.
# --------------------------------------------------------------------------


def _callee(node: ast.AST) -> str | None:
    """``mapped_column`` for ``mapped_column(...)``, ``sa.Column(...)`` → ``Column``."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


@dataclass
class _Class:
    table: str | None
    columns: set[str]
    bases: list[str]


def read_models(sources: dict[str, str]) -> dict[str, set[str]]:
    """``{table: {column, ...}}`` mapped by the given model sources.

    Columns a mixin declares count for every table class that inherits it; a
    base that has a table of its own (joined inheritance) does not lend its
    columns. ``Table("name", metadata, Column("c"), ...)`` counts too.
    """
    # Keyed by (file, class name): two models may share a class name
    # (room_task's Task maps `tasks`, task's Task maps `task`).
    classes: dict[tuple[str, str], _Class] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}
    tables: dict[str, set[str]] = {}
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                info = _Class(None, set(), [])
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        info.bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        info.bases.append(base.attr)
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                        target, value = stmt.targets[0], stmt.value
                    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                        target, value = stmt.target, stmt.value
                    else:
                        continue
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id == "__tablename__" and _str(value):
                        info.table = _str(value)
                    elif _callee(value) in {"mapped_column", "Column"}:
                        first = value.args[0] if value.args else None  # type: ignore[attr-defined]
                        info.columns.add(_str(first) or target.id)
                classes[(path, node.name)] = info
                by_name.setdefault(node.name, []).append((path, node.name))
            elif _callee(node) == "Table" and node.args and _str(node.args[0]):  # type: ignore[attr-defined]
                cols = tables.setdefault(_str(node.args[0]), set())  # type: ignore[attr-defined,arg-type]
                for arg in node.args[1:]:  # type: ignore[attr-defined]
                    if _callee(arg) == "Column" and arg.args and _str(arg.args[0]):  # type: ignore[attr-defined]
                        cols.add(_str(arg.args[0]))  # type: ignore[attr-defined,arg-type]

    def inherited(key: tuple[str, str], seen: set[tuple[str, str]]) -> set[str]:
        # A base named in several files: take all of them. Over-reporting a
        # still-mapped column is the safe direction for this check.
        out: set[str] = set()
        for base in classes[key].bases:
            for base_key in by_name.get(base, []):
                if base_key in seen or classes[base_key].table:
                    continue
                seen.add(base_key)
                out |= classes[base_key].columns | inherited(base_key, seen)
        return out

    for key, info in classes.items():
        if info.table:
            tables.setdefault(info.table, set()).update(info.columns | inherited(key, {key}))
    return tables


# --------------------------------------------------------------------------
# One migration file.
# --------------------------------------------------------------------------

_SQL_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP\s+|TEMPORARY\s+|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)", re.I
)
_SQL_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w\",.\s]+?)(?:\s+CASCADE|\s*$)", re.I)
_SQL_ALTER_TABLE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?(.*)$", re.I | re.S
)
_SQL_DROP_COLUMN = re.compile(r"\bDROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?\"?(\w+)", re.I)
_SQL_RENAME_COLUMN = re.compile(r"\bRENAME\s+(?:COLUMN\s+)?\"?(\w+)\"?\s+TO\b", re.I)
_SQL_RENAME_TABLE = re.compile(r"^\s*RENAME\s+TO\b", re.I)
_SQL_ADD_CONSTRAINT = re.compile(
    r"\bADD\s+(?:CONSTRAINT\s+\"?\w+\"?\s+)?(FOREIGN\s+KEY|CHECK|UNIQUE)\b", re.I
)
_SQL_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"?\w+\"?\s+)?ON\s+(?:ONLY\s+)?\"?(\w+)\"?(?:\s+USING\s+\w+)?\s*\(\s*\"?(\w+)?",
    re.I,
)


HOLE = "{…}"


def _sql_text(node: ast.AST | None, constants: dict[str, ast.AST] | None = None) -> str | None:
    """The SQL a ``op.execute`` argument spells, with ``{…}`` for f-string holes.

    A bare name resolves through ``constants`` (the module's top-level string
    assignments); anything else unreadable is ``None``.
    """
    if node is None:
        return None
    if _str(node) is not None:
        return _str(node)
    if isinstance(node, ast.JoinedStr):
        return "".join(_str(v) if _str(v) is not None else HOLE for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _sql_text(node.left, constants), _sql_text(node.right, constants)
        return None if left is None or right is None else left + right
    if _callee(node) == "text" and node.args:  # type: ignore[attr-defined]
        return _sql_text(node.args[0], constants)  # type: ignore[attr-defined]
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return _sql_text(constants[node.id], None)
    return None


def _module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            out[stmt.targets[0].id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            out[stmt.target.id] = stmt.value
    return out


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _truthy(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value)


def _is_json_type(node: ast.AST | None) -> bool:
    """``sa.JSON`` / ``sa.JSON()`` / ``JSON(none_as_null=True)`` — not JSONB."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr == "JSON"
    if isinstance(node, ast.Name):
        return node.id == "JSON"
    return False


@dataclass
class _Column:
    name: str
    line: int
    nullable: bool
    server_default: bool
    is_json: bool
    foreign_key: bool
    indexed: bool
    primary: bool = False


def _column(call: ast.Call) -> _Column | None:
    """``sa.Column("name", type, ...)`` as the facts the rules need."""
    if _callee(call) != "Column" or not call.args or _str(call.args[0]) is None:
        return None
    rest = call.args[1:]
    primary = _truthy(_kw(call, "primary_key"))
    nullable_kw = _kw(call, "nullable")
    nullable = not primary if nullable_kw is None else _truthy(nullable_kw)
    return _Column(
        name=_str(call.args[0]),  # type: ignore[arg-type]
        line=call.lineno,
        nullable=nullable,
        server_default=_kw(call, "server_default") is not None,
        is_json=bool(rest) and _is_json_type(rest[0]),
        foreign_key=any(_callee(a) == "ForeignKey" for a in rest),
        indexed=_truthy(_kw(call, "index")) or _truthy(_kw(call, "unique")),
        primary=primary,
    )


def _leading_column(node: ast.AST | None) -> str | None:
    """First column of a ``["a", "b"]`` list, or ``"a"`` itself."""
    if isinstance(node, ast.List | ast.Tuple) and node.elts:
        node = node.elts[0]
    if isinstance(node, ast.Call) and node.args:  # sa.text("lower(x)") etc.
        return None
    return _str(node)


@dataclass
class _FileFacts:
    """What one migration does, gathered before any rule is judged."""

    created_tables: set[str] = field(default_factory=set)
    indexed: set[tuple[str, str]] = field(default_factory=set)
    fk_columns: list[tuple[str, str, int]] = field(default_factory=list)


class _Migration(ast.NodeVisitor):
    def __init__(
        self,
        path: str,
        source: str,
        base_tables: dict[str, set[str]],
        new_tables: set[str],
        facts: _FileFacts,
    ) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.base_tables = base_tables
        self.new_tables = new_tables
        self.facts = facts
        self.findings: list[Finding] = []
        self._autocommit = 0
        self.constants: dict[str, ast.AST] = {}
        self._docstrings: set[int] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self.constants = _module_constants(node)
        for scope in ast.walk(node):
            if isinstance(scope, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                body = scope.body
                if body and isinstance(body[0], ast.Expr) and _str(body[0].value) is not None:
                    self._docstrings.add(id(body[0].value))
        self.generic_visit(node)

    # -- plumbing --------------------------------------------------------

    def report(self, node: ast.AST, rule: str, message: str) -> None:
        self.findings.append(Finding(self.path, getattr(node, "lineno", 1), rule, message))

    def existing(self, table: str | None) -> bool:
        return table is not None and table not in self.new_tables

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "downgrade":
            return
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        autocommit = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "autocommit_block"
            for item in node.items
        )
        self._autocommit += autocommit
        self.generic_visit(node)
        self._autocommit -= autocommit

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "app" or alias.name.startswith("app."):
                self.report(node, "app-import", f"imports {alias.name}; copy what you need into the migration")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and (node.module == "app" or node.module.startswith("app.")) and node.level == 0:
            self.report(node, "app-import", f"imports from {node.module}; copy what you need into the migration")

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self._docstrings:
            return
        if isinstance(node.value, str) and "lock_not_available" in node.value:
            self.report(node, "lock-retry-copy", "a pasted lock-retry loop; call migration_helpers.with_lock_retries")

    # -- op.* ------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "op":
            handler = getattr(self, f"_op_{func.attr}", None)
            if handler is not None:
                handler(node)
        self.generic_visit(node)

    def _gone_from_base(self, node: ast.AST, rule: str, table: str | None, column: str | None) -> None:
        if table is None or (column is None and rule != "drop-table"):
            # A drop the check cannot read is the #2914 accident it exists for:
            # an error, which a reasoned exception can waive.
            self.report(node, "unresolved-drop", f"{rule}: could not read the table/column names; spell them as literals")
            return
        mapped = self.base_tables.get(table)
        if mapped is None:
            return
        if column is None:
            self.report(
                node,
                rule,
                f"table {table} is still mapped by the release this deploy replaces; "
                "remove the model first and drop the table in a later PR",
            )
        elif column in mapped:
            self.report(
                node,
                rule,
                f"column {table}.{column} is still mapped by the release this deploy replaces; "
                "stop mapping it first and drop/rename it in a later PR",
            )

    def _op_drop_column(self, node: ast.Call) -> None:
        args = node.args
        self._gone_from_base(node, "drop-column", _str(args[0]) if args else None, _str(args[1]) if len(args) > 1 else None)

    def _op_drop_table(self, node: ast.Call) -> None:
        self._gone_from_base(node, "drop-table", _str(node.args[0]) if node.args else None, None)

    def _op_rename_table(self, node: ast.Call) -> None:
        self._gone_from_base(node, "drop-table", _str(node.args[0]) if node.args else None, None)
        if self.findings and self.findings[-1].rule == "drop-table" and self.findings[-1].line == node.lineno:
            self.findings[-1].rule = "rename"

    def _op_alter_column(self, node: ast.Call) -> None:
        table = _str(node.args[0]) if node.args else None
        column = _str(node.args[1]) if len(node.args) > 1 else None
        if _kw(node, "new_column_name") is not None:
            self._gone_from_base(node, "rename", table, column)
        if not self.existing(table):
            return
        if _kw(node, "type_") is not None or (
            isinstance(_kw(node, "nullable"), ast.Constant) and _kw(node, "nullable").value is False  # type: ignore[union-attr]
        ):
            self.report(
                node,
                "alter-column-existing",
                f"changing the type or setting NOT NULL on {table}.{column} rewrites or scans it under lock; "
                "see the rules for the CHECK … NOT VALID route",
            )

    def _op_create_index(self, node: ast.Call) -> None:
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        columns = node.args[2] if len(node.args) > 2 else _kw(node, "columns")
        if table and _leading_column(columns):
            self.facts.indexed.add((table, _leading_column(columns)))  # type: ignore[arg-type]
        if not self.existing(table):
            return
        if not _truthy(_kw(node, "postgresql_concurrently")):
            self.report(
                node,
                "index-not-concurrent",
                f"index on existing table {table} without postgresql_concurrently=True blocks its writes",
            )
        elif not self._autocommit:
            self.report(
                node,
                "index-outside-autocommit",
                "CREATE INDEX CONCURRENTLY cannot run in a transaction; "
                "put it in `with op.get_context().autocommit_block():`",
            )

    def _op_create_foreign_key(self, node: ast.Call) -> None:
        source = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "source_table"))
        if self.existing(source) and not _truthy(_kw(node, "postgresql_not_valid")):
            self.report(
                node,
                "constraint-not-valid",
                f"foreign key on existing table {source} without postgresql_not_valid=True scans it under lock; "
                "add it NOT VALID, then VALIDATE CONSTRAINT",
            )

    def _op_create_check_constraint(self, node: ast.Call) -> None:
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        if self.existing(table) and not _truthy(_kw(node, "postgresql_not_valid")):
            self.report(
                node,
                "constraint-not-valid",
                f"CHECK on existing table {table} without postgresql_not_valid=True scans it under lock; "
                "add it NOT VALID, then VALIDATE CONSTRAINT",
            )

    def _op_create_unique_constraint(self, node: ast.Call) -> None:
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        if self.existing(table):
            self.report(
                node,
                "unique-constraint",
                f"UNIQUE constraint on existing table {table} builds its index under lock; "
                "CREATE UNIQUE INDEX CONCURRENTLY, then ADD CONSTRAINT … USING INDEX",
            )

    def _op_add_column(self, node: ast.Call) -> None:
        table = _str(node.args[0]) if node.args else None
        column = _column(node.args[1]) if len(node.args) > 1 and isinstance(node.args[1], ast.Call) else None
        if table is None or column is None:
            return
        self._column_rules(node, table, column)
        if self.existing(table):
            if not column.nullable and not column.server_default:
                self.report(
                    node,
                    "add-column-not-null",
                    f"NOT NULL column {table}.{column.name} without server_default; "
                    "the previous release inserts rows without it",
                )
            if column.foreign_key:
                self.report(
                    node,
                    "fk-on-add-column",
                    f"{table}.{column.name} adds a validated foreign key; lock the referenced table "
                    "with with_lock_retries, or add the FK NOT VALID separately",
                )

    def _op_create_table(self, node: ast.Call) -> None:
        table = _str(node.args[0]) if node.args else None
        if table is None:
            return
        first_primary = True
        for arg in node.args[1:]:
            if not isinstance(arg, ast.Call):
                continue
            column = _column(arg)
            if column is not None:
                # A composite primary key indexes its first column only.
                if column.primary and first_primary:
                    column.indexed = True
                    first_primary = False
                self._column_rules(arg, table, column)
                continue
            kind = _callee(arg)
            leading = _leading_column(arg.args[0]) if arg.args else None
            if kind in {"PrimaryKeyConstraint", "UniqueConstraint"} and _str(arg.args[0] if arg.args else None):
                self.facts.indexed.add((table, _str(arg.args[0])))  # type: ignore[arg-type]
            elif kind == "Index" and len(arg.args) > 1 and _str(arg.args[1]):
                self.facts.indexed.add((table, _str(arg.args[1])))  # type: ignore[arg-type]
            elif kind == "ForeignKeyConstraint" and leading:
                self.facts.fk_columns.append((table, leading, arg.lineno))

    def _column_rules(self, node: ast.AST, table: str, column: _Column) -> None:
        if column.is_json:
            self.report(node, "json-not-jsonb", f"{table}.{column.name} is JSON; use postgresql.JSONB")
        if column.indexed:
            self.facts.indexed.add((table, column.name))
        if column.foreign_key:
            self.facts.fk_columns.append((table, column.name, column.line))

    def _sql_add_column(self, node: ast.Call, table: str, name: str, spec: str) -> None:
        upper = " " + re.sub(r"\s+", " ", spec.upper()) + " "
        column = _Column(
            name=name,
            line=node.lineno,
            nullable=" NOT NULL " not in upper and " PRIMARY KEY " not in upper,
            server_default=" DEFAULT " in upper,
            is_json=bool(re.match(r"\s*JSON\b", spec, re.I)),
            foreign_key=" REFERENCES " in upper,
            indexed=" PRIMARY KEY " in upper or " UNIQUE " in upper,
        )
        self._column_rules(node, table, column)
        if self.existing(table):
            if not column.nullable and not column.server_default:
                self.report(
                    node,
                    "add-column-not-null",
                    f"NOT NULL column {table}.{name} without DEFAULT; the previous release inserts rows without it",
                )
            if column.foreign_key:
                self.report(
                    node,
                    "fk-on-add-column",
                    f"{table}.{name} adds a validated foreign key; lock the referenced table "
                    "with with_lock_retries, or add the FK NOT VALID separately",
                )

    def _op_execute(self, node: ast.Call) -> None:
        sql = _sql_text(node.args[0] if node.args else None, self.constants)
        if sql is None:
            self.report(node, "unresolved", "could not read this SQL statically; check it against the rules by hand")
            return
        for statement in _strip_sql_comments(sql).split(";"):
            statement = statement.strip()
            if _hole_in_a_name(statement):
                self.report(
                    node,
                    "unresolved",
                    "SQL built from an f-string whose hole names the table or a whole clause; check it by hand",
                )
            self._statement(node, statement)

    def _statement(self, node: ast.Call, sql: str) -> None:
        if not sql:
            return
        if match := _SQL_CREATE_INDEX.search(sql):
            concurrently, table, leading = match.group(1), match.group(2), match.group(3)
            if leading:
                self.facts.indexed.add((table, leading))
            if self.existing(table):
                if not concurrently:
                    self.report(node, "index-not-concurrent", f"CREATE INDEX on existing table {table} without CONCURRENTLY")
                elif not self._autocommit:
                    self.report(
                        node,
                        "index-outside-autocommit",
                        "CREATE INDEX CONCURRENTLY must run in `with op.get_context().autocommit_block():`",
                    )
        if match := _SQL_DROP_TABLE.search(sql):
            for name in re.split(r"[\s,]+", match.group(1).replace('"', "")):
                name = name.rsplit(".", 1)[-1]
                if name:
                    self._gone_from_base(node, "drop-table", name, None)
        if match := _SQL_ALTER_TABLE.search(sql):
            table, rest = match.group(1), match.group(2)
            for clause in _split_clauses(rest):
                if _SQL_RENAME_TABLE.search(clause):
                    self._gone_from_base(node, "drop-table", table, None)
                    if self.findings and self.findings[-1].line == node.lineno and self.findings[-1].rule == "drop-table":
                        self.findings[-1].rule = "rename"
                    continue
                if (column := _SQL_RENAME_COLUMN.search(clause)) is not None:
                    self._gone_from_base(node, "rename", table, column.group(1))
                    continue
                if re.match(r"\s*DROP\s+(?!CONSTRAINT|DEFAULT|NOT\s+NULL|TRIGGER)", clause, re.I):
                    column = _SQL_DROP_COLUMN.search(clause)
                    if column:
                        self._gone_from_base(node, "drop-column", table, column.group(1))
                    continue
                if (added := _SQL_ADD_COLUMN.match(clause)) is not None:
                    self._sql_add_column(node, table, added.group(1), added.group(2))
                    continue
                constraint = _SQL_ADD_CONSTRAINT.search(clause)
                if constraint is None or not self.existing(table):
                    continue
                kind = constraint.group(1).upper()
                if kind == "UNIQUE":
                    if not re.search(r"\bUSING\s+INDEX\b", clause, re.I):
                        self.report(
                            node,
                            "unique-constraint",
                            f"ADD UNIQUE on existing table {table}; build the index CONCURRENTLY and ADD … USING INDEX",
                        )
                elif not re.search(r"\bNOT\s+VALID\b", clause, re.I):
                    self.report(
                        node,
                        "constraint-not-valid",
                        f"ADD {kind} on existing table {table} without NOT VALID scans it under lock",
                    )
        if match := _SQL_CREATE_TABLE.search(sql):
            self.facts.created_tables.add(match.group(1))


_SQL_ADD_COLUMN = re.compile(
    r"\s*ADD\s+(?:COLUMN\s+)?(?!CONSTRAINT\b|PRIMARY\b|UNIQUE\b|CHECK\b|FOREIGN\b|EXCLUDE\b)"
    r"(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s+(.*)$",
    re.I | re.S,
)


def _hole_in_a_name(statement: str) -> bool:
    """An f-string hole where a table name or a whole clause goes."""
    hole = re.escape(HOLE)
    return bool(
        re.match(rf"\s*{hole}", statement)
        or re.search(rf"\b(?:TABLE|ON|INTO|UPDATE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:ONLY\s+)?\S*{hole}", statement, re.I)
        or re.match(rf"\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?\S+\s+{hole}", statement, re.I)
    )


def _split_clauses(rest: str) -> list[str]:
    """``ALTER TABLE t a, b(c, d), e`` → ``[a, b(c, d), e]`` (commas at depth 0)."""
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


def created_tables(source: str) -> set[str]:
    """Tables a migration creates (``op.create_table`` or ``CREATE TABLE``)."""
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            node.body = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and _str(node.args[0])
        ):
            out.add(_str(node.args[0]))  # type: ignore[arg-type]
        sql = _sql_text(node) if isinstance(node, ast.Constant | ast.JoinedStr) else None
        if sql:
            out.update(_SQL_CREATE_TABLE.findall(_strip_sql_comments(sql)))
    return out


def _allowed(lines: list[str], finding: Finding, node_span: tuple[int, int]) -> bool:
    for line in lines:
        match = _PRAGMA.search(line)
        if match and match.group(1) == "allow-file" and finding.rule in _rules(match.group(2)):
            return True
    first, last = node_span
    for number in range(max(first - 1, 1), last + 1):
        if number - 1 >= len(lines):
            break
        match = _PRAGMA.search(lines[number - 1])
        if match and match.group(1) == "allow" and finding.rule in _rules(match.group(2)):
            return True
    return False


def _rules(spec: str) -> set[str]:
    return {r.strip() for r in re.split(r"[,\s]+", spec) if r.strip()}


def analyze(migrations: dict[str, str], base_models: dict[str, str]) -> list[Finding]:
    """Findings for the given migration sources, judged against ``base_models``."""
    base_tables = read_models(base_models)
    new_tables: set[str] = set()
    for source in migrations.values():
        new_tables |= created_tables(source)
    findings: list[Finding] = []
    indexed_anywhere: set[tuple[str, str]] = set()
    pending: list[tuple[list[str], dict[int, tuple[int, int]], list[Finding], _FileFacts]] = []
    for path, source in sorted(migrations.items()):
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            findings.append(Finding(path, error.lineno or 1, "unresolved", f"does not parse: {error}"))
            continue
        facts = _FileFacts()
        visitor = _Migration(path, source, base_tables, new_tables, facts)
        visitor.visit(tree)
        indexed_anywhere |= facts.indexed
        # A statement's span: the widest node starting on its first line, so an
        # exception on any line of a multi-line call counts.
        spans: dict[int, tuple[int, int]] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.stmt | ast.expr):
                end = n.end_lineno or n.lineno
                if end > spans.get(n.lineno, (n.lineno, n.lineno))[1]:
                    spans[n.lineno] = (n.lineno, end)
        pending.append((source.splitlines(), spans, visitor.findings, facts))
        visitor.findings.extend(
            Finding(path, line, "fk-without-index", f"__fk__{table}.{column}")
            for table, column, line in facts.fk_columns
        )
    for lines, spans, file_findings, _facts in pending:
        for finding in file_findings:
            if finding.rule == "fk-without-index" and finding.message.startswith("__fk__"):
                table, column = finding.message.removeprefix("__fk__").split(".", 1)
                # An index any migration of this branch builds counts.
                if (table, column) in indexed_anywhere:
                    continue
                finding.message = (
                    f"foreign key column {table}.{column} has no index starting with it; add index=True or an Index"
                )
            if not _allowed(lines, finding, spans.get(finding.line, (finding.line, finding.line))):
                findings.append(finding)
    return findings


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def read_tree(ref: str, directory: str) -> dict[str, str]:
    """Every ``.py`` under ``directory`` at ``ref``, read in one ``cat-file`` pass."""
    paths = [p for p in _git("ls-tree", "-r", "--name-only", ref, f"{directory}/").splitlines() if p.endswith(".py")]
    if not paths:
        return {}
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="".join(f"{ref}:{p}\n" for p in paths).encode(),
        check=True,
        capture_output=True,
    ).stdout
    sources: dict[str, str] = {}
    offset = 0
    for path in paths:
        header_end = batch.index(b"\n", offset)
        size = int(batch[offset:header_end].split()[2])
        start = header_end + 1
        sources[path] = batch[start : start + size].decode("utf-8", errors="replace")
        offset = start + size + 1
    return sources


def added_migrations(base: str, versions_dir: str) -> list[str]:
    merge_base = _git("merge-base", base, "HEAD").strip()
    added = _git("diff", "--name-only", "--diff-filter=A", merge_base, "--", f"{versions_dir}/").splitlines()
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", f"{versions_dir}/").splitlines()
    return sorted({p for p in added + untracked if p.endswith(".py")})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--versions-dir", default=VERSIONS_DIR)
    parser.add_argument("--models-dir", default=MODELS_DIR)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    import os

    files = [os.path.abspath(f) for f in args.files]
    # Paths below are repository paths, whatever directory this runs from.
    os.chdir(_git("rev-parse", "--show-toplevel").strip())
    try:
        base_models = read_tree(args.base, args.models_dir)
        paths = files or added_migrations(args.base, args.versions_dir)
    except subprocess.CalledProcessError:
        if os.environ.get("CI"):
            print(f"FAIL: {args.base} is not available; fetch it before this step (git fetch --unshallow origin)")
            return 2
        if not files:
            print(f"note: {args.base} not available — cannot tell which migrations are new, skipping")
            return 0
        print(f"note: {args.base} not available — drop/rename rules skipped, the rest still apply")
        base_models, paths = {}, files

    if not paths:
        print(f"PASS: no migrations added relative to {args.base}")
        return 0

    migrations = {p: Path(p).read_text(encoding="utf-8") for p in paths if Path(p).is_file()}
    findings = analyze(migrations, base_models)
    errors = [f for f in findings if f.is_error]
    for finding in findings:
        level = "error" if finding.is_error else "warning"
        print(f"{finding.path}:{finding.line}: {level} [{finding.rule}] {finding.message}")
        print(f"::{level} file={finding.path},line={finding.line}::[{finding.rule}] {finding.message}")
    if errors:
        print()
        print(f"FAIL: {len(errors)} unsafe statement(s) in {len(migrations)} added migration(s).")
        print("The previous release is still serving while migrations run; see .claude/rules/migrations.md.")
        print("A justified exception: `# migration-safety: allow <rule> — <reason>` on that statement.")
        return 1
    print(f"PASS: {len(migrations)} added migration(s) safe to run under the previous release")
    return 0


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------

_BASE_MODELS = {
    "backend/app/domain/task/models.py": '''
from sqlalchemy.orm import Mapped, mapped_column
class Stamped:
    created_at: Mapped[int] = mapped_column(Integer)
class Task(Stamped, Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_url: Mapped[str] = mapped_column(String)
    renamed: Mapped[str] = mapped_column("legacy_name", String)
legacy = Table("legacy", Base.metadata, Column("x", Integer))
''',
}

_HEAD = '''
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
revision = "b"
down_revision = "a"
'''


def _rules_found(body: str, extra: str = "") -> list[str]:
    source = _HEAD + extra + "\ndef upgrade() -> None:\n" + body + "\n\ndef downgrade() -> None:\n    op.drop_column('task', 'video_url')\n"
    return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS))


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # The three the completion standard names.
    check("drop a mapped column", _rules_found("    op.drop_column('task', 'video_url')"), ["drop-column"])
    check(
        "index on an existing table, not concurrent",
        _rules_found("    op.create_index('ix_task_video_url', 'task', ['video_url'])"),
        ["index-not-concurrent"],
    )
    check(
        "foreign key on an existing table, validated",
        _rules_found("    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'])"),
        ["constraint-not-valid"],
    )

    # What must stay quiet.
    check("drop a column the model no longer maps", _rules_found("    op.drop_column('task', 'gone')"), [])
    check("drop a mixin column still mapped", _rules_found("    op.drop_column('task', 'created_at')"), ["drop-column"])
    check("the mapped name is the column name", _rules_found("    op.drop_column('task', 'legacy_name')"), ["drop-column"])
    check("downgrade() is not judged", _rules_found("    pass"), [])
    check(
        "concurrent index in an autocommit block",
        _rules_found(
            "    with op.get_context().autocommit_block():\n"
            "        op.create_index('ix', 'task', ['video_url'], postgresql_concurrently=True)"
        ),
        [],
    )
    check(
        "concurrent index outside an autocommit block",
        _rules_found("    op.create_index('ix', 'task', ['video_url'], postgresql_concurrently=True)"),
        ["index-outside-autocommit"],
    )
    check(
        "a table created here may be indexed plainly, its FK column indexed",
        _rules_found(
            "    op.create_table('fresh', sa.Column('id', sa.Uuid(), primary_key=True),"
            " sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id')))\n"
            "    op.create_index('ix_fresh_task_id', 'fresh', ['task_id'])"
        ),
        [],
    )
    check(
        "a new FK column with no index",
        _rules_found(
            "    op.create_table('fresh', sa.Column('id', sa.Uuid(), primary_key=True),"
            " sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id')))"
        ),
        ["fk-without-index"],
    )
    check(
        "a new JSON column",
        _rules_found("    op.add_column('task', sa.Column('meta', sa.JSON(), nullable=True))"),
        ["json-not-jsonb"],
    )
    check(
        "NOT NULL without a default on an existing table",
        _rules_found("    op.add_column('task', sa.Column('n', sa.Integer(), nullable=False))"),
        ["add-column-not-null"],
    )
    check(
        "NOT NULL with a server default",
        _rules_found("    op.add_column('task', sa.Column('n', sa.Integer(), nullable=False, server_default='0'))"),
        [],
    )
    check("drop a mapped table", _rules_found("    op.drop_table('legacy')"), ["drop-table"])
    check("rename a mapped column", _rules_found("    op.alter_column('task', 'video_url', new_column_name='v')"), ["rename"])
    check("app import", _rules_found("    pass", "from app.domain.task.models import Task\n"), ["app-import"])
    check(
        "unique constraint on an existing table",
        _rules_found("    op.create_unique_constraint('uq', 'task', ['video_url'])"),
        ["unique-constraint"],
    )
    check(
        "raw SQL: the same three",
        _rules_found(
            '    op.execute("ALTER TABLE task DROP COLUMN video_url")\n'
            '    op.execute("CREATE INDEX ix_t ON task (video_url)")\n'
            '    op.execute("ALTER TABLE task ADD CONSTRAINT fk FOREIGN KEY (owner_id) REFERENCES users (id)")'
        ),
        ["constraint-not-valid", "drop-column", "index-not-concurrent"],
    )
    check(
        "raw SQL done right",
        _rules_found(
            '    op.execute("ALTER TABLE task ADD CONSTRAINT fk FOREIGN KEY (owner_id) REFERENCES users (id) NOT VALID")\n'
            '    op.execute("ALTER TABLE task VALIDATE CONSTRAINT fk")\n'
            '    op.execute("ALTER TABLE task DROP CONSTRAINT old_fk")\n'
            "    with op.get_context().autocommit_block():\n"
            '        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_t ON task (video_url)")'
        ),
        [],
    )
    check(
        "a temporary table is new",
        _rules_found(
            '    op.execute("CREATE TEMP TABLE scratch ON COMMIT DROP AS SELECT 1 AS x")\n'
            '    op.execute("CREATE INDEX ON scratch (x)")'
        ),
        [],
    )
    # Found in review (2026-10-07).
    two_tasks = {
        "backend/app/domain/task/models.py": 'class Task(Base):\n    __tablename__ = "task"\n',
        "backend/app/domain/room_task/models.py": (
            'class Task(Base):\n    __tablename__ = "tasks"\n    project_id = mapped_column(Uuid)\n'
        ),
    }
    check("same class name in two files", sorted(read_models(two_tasks)), ["task", "tasks"])
    check(
        "raw ADD COLUMN: NOT NULL, JSON, an unindexed reference",
        _rules_found(
            '    op.execute("ALTER TABLE task ADD COLUMN z int NOT NULL")\n'
            '    op.execute("ALTER TABLE task ADD COLUMN j json")\n'
            '    op.execute("ALTER TABLE task ADD COLUMN IF NOT EXISTS pid uuid REFERENCES projects(id)")'
        ),
        ["add-column-not-null", "fk-on-add-column", "fk-without-index", "json-not-jsonb"],
    )
    check(
        "raw ADD COLUMN done right",
        _rules_found('    op.execute("ALTER TABLE task ADD COLUMN z int NOT NULL DEFAULT 0, ADD COLUMN j jsonb")'),
        [],
    )
    check(
        "a module constant is read",
        _rules_found("    op.execute(DROP)", 'DROP = "ALTER TABLE task DROP COLUMN video_url"\n'),
        ["drop-column"],
    )
    check(
        "a hole where the clause goes is reported",
        _rules_found('    for clause in ("DROP COLUMN video_url",):\n        op.execute(f"ALTER TABLE task {clause}")'),
        ["unresolved"],
    )
    check(
        "unreadable SQL is reported",
        _rules_found("    op.execute(make_sql())"),
        ["unresolved"],
    )
    check(
        "a hole in a value is fine",
        _rules_found('    op.execute(f"UPDATE task SET video_url = NULL WHERE id = {1}")'),
        [],
    )
    check(
        "composite primary key: the second column is not indexed",
        _rules_found(
            "    op.create_table('link', sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id'), primary_key=True),"
            " sa.Column('tag_id', sa.Integer(), sa.ForeignKey('tag.id'), primary_key=True))"
        ),
        ["fk-without-index"],
    )
    check(
        "an exception on a later line of a multi-line call",
        _rules_found(
            "    op.drop_column(\n        'task',\n        'video_url',  # migration-safety: allow drop-column — unmapped in #1\n    )"
        ),
        [],
    )
    check(
        "an index in another added migration counts",
        sorted(
            f.rule
            for f in analyze(
                {
                    "a.py": _HEAD + "def upgrade():\n    op.create_table('fresh', sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id')))\n",
                    "b.py": _HEAD + "def upgrade():\n    op.create_index('ix', 'fresh', ['task_id'])\n",
                },
                _BASE_MODELS,
            )
        ),
        [],
    )
    check("an unreadable drop", _rules_found("    op.drop_column('task', COLUMN)"), ["unresolved-drop"])
    check(
        "a schema-qualified table",
        _rules_found('    op.execute("ALTER TABLE public.task DROP COLUMN video_url")\n    op.execute("DROP TABLE public.legacy")'),
        ["drop-column", "drop-table"],
    )
    check(
        "a docstring that mentions lock_not_available",
        [f.rule for f in analyze({"d.py": '"""We retry on lock_not_available."""\n' + _HEAD}, _BASE_MODELS)],
        [],
    )
    check(
        "a pasted lock-retry loop",
        _rules_found('    op.execute("DO $$ BEGIN EXCEPTION WHEN lock_not_available THEN END $$")'),
        ["lock-retry-copy"],
    )
    check(
        "an exception with a reason",
        _rules_found("    op.drop_column('task', 'video_url')  # migration-safety: allow drop-column — not read since #1"),
        [],
    )
    check(
        "an exception without a reason does not count",
        _rules_found("    op.drop_column('task', 'video_url')  # migration-safety: allow drop-column"),
        ["drop-column"],
    )
    check(
        "a file-wide exception",
        _rules_found(
            "    op.create_index('ix', 'task', ['video_url'])",
            "# migration-safety: allow-file index-not-concurrent — task has 3 rows\n",
        ),
        [],
    )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print("PASS: check-migration-safety self-test (drop/rename, index, constraints, columns, imports, exceptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
