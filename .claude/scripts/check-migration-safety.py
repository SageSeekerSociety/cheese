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
  validate-outside-autocommit
      A ``VALIDATE CONSTRAINT`` sharing a migration with the ``NOT VALID``
      constraint it follows runs in that migration's transaction, holds the
      lock to the end of it and cancels the ``NOT VALID`` gain. Validate in
      ``op.get_context().autocommit_block()``.
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
  unresolved-table
      A statement whose table name is not a literal cannot be checked against
      the previous release; spell the name out (or justify an exception). The
      same bargain ``unresolved-drop`` makes, one step earlier: a name the
      check cannot read is a lock risk it cannot clear.
  unresolved-drop
      A drop or rename whose table or column is not a literal cannot be
      checked; spell the names out (or justify an exception).
  unparseable
      A migration file does not parse as Python. Every statement in it is
      unjudged, so it is an error, not the warning ``unresolved`` gives a
      statement whose SQL is merely dynamic.
  lock-retry-copy
      Lock retries come from ``migration_helpers.with_lock_retries``, not a
      pasted ``DO`` block.
  fk-table-not-locked
      An ``upgrade()`` that calls ``with_lock_retries`` and then, in the same
      transaction, adds or drops a foreign key (``create_foreign_key``,
      ``drop_constraint``, a key inline in ``add_column`` / ``create_table``,
      or the raw SQL for either) must name both of its tables in that call. A
      table left out is locked later, while the listed ones are already held,
      and deadlocks with the live backend reading them in the other order
      (``b8d3f1a6c254``: ``topics``). A dropped key's target is read from the
      ``upgrade()`` that created it; one that cannot be found, or was created
      pointing at more than one table, is an ``unresolved`` warning. An
      ``upgrade()`` that never calls ``with_lock_retries`` is not judged, nor
      is the key a ``drop_column`` / ``drop_table`` removes along with it.
  empty-downgrade
      A migration whose ``downgrade()`` does nothing — a lone ``pass``, once
      its docstring is set aside — must say why in that docstring: what it
      deliberately leaves in place, or a ``raise``. The Alembic template's
      ``Downgrade schema.`` is not a reason.

Only migrations ADDED relative to the merge base with ``--base`` are judged:
the ones on main are immutable, and judging them would make every PR pay for
history. What a ``downgrade()`` *does* is still not judged for the risks
above; only whether an empty one explains itself (``empty-downgrade``) is.

An exception is written next to the statement it excuses, with a reason:

    op.drop_column("t", "c")  # migration-safety: allow drop-column — <why>

(on the statement's lines or the line above), or once for the whole file:

    # migration-safety: allow-file index-not-concurrent — <why>

Usage:
    check-migration-safety.py [--base origin/main] [--versions-dir DIR] [FILE ...]
    check-migration-safety.py --self-test

FILEs, when given, are judged instead of the added ones (still against
``--base``'s models).

Exit 0 nothing unsafe was read, 1 something was, 2 ``--base`` could not be read,
so nothing was judged.
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

VERSIONS_DIR = "backend/alembic/versions"
MODELS_DIR = "backend/app"

# Exit 2 is this directory's "could not judge" (check-file-sizes.py
# and the ratchets all use it). A guard that could not read its comparison base has
# judged no migration at all, so exiting 0 there is indistinguishable from a run
# that read the diffs and cleared them. Distinct from 1, which means migrations were
# read and refused, so a caller can tell "could not answer" from "answered no".
EXIT_CANNOT_JUDGE = 2

ERROR_RULES = frozenset(
    {
        "drop-column",
        "drop-table",
        "rename",
        "index-not-concurrent",
        "index-outside-autocommit",
        "constraint-not-valid",
        "validate-outside-autocommit",
        "unique-constraint",
        "add-column-not-null",
        "fk-without-index",
        "json-not-jsonb",
        "app-import",
        "lock-retry-copy",
        "fk-table-not-locked",
        "unresolved-table",
        "unresolved-drop",
        "unparseable",
        "empty-downgrade",
    }
)

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
_SQL_VALIDATE_CONSTRAINT = re.compile(r"\bVALIDATE\s+CONSTRAINT\b", re.I)
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


# --------------------------------------------------------------------------
# ``downgrade()``: an empty one has to say why (the ``empty-downgrade`` rule).
# --------------------------------------------------------------------------

#: What Alembic's own template leaves in a new downgrade. Carries no reason.
_DOWNGRADE_TEMPLATE = "downgrade schema"


def _downgrade_docstring(node: ast.FunctionDef) -> str | None:
    """The string that opens ``node``'s body as a docstring, or ``None``."""
    body = node.body
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _is_a_reason(doc: str | None) -> bool:
    """Whether ``doc`` states a reason — not blank, not the template."""
    if doc is None or not doc.strip():
        return False
    return re.sub(r"\s+", " ", doc.strip()).rstrip(".").lower() != _DOWNGRADE_TEMPLATE


def _downgrade_is_empty(node: ast.FunctionDef) -> bool:
    """A ``downgrade()`` that does nothing (a lone ``pass``) and gives no reason.

    The docstring is set aside first, so the Alembic template text followed by
    ``pass`` counts as empty: writing the template is not saying why.
    """
    doc = _downgrade_docstring(node)
    body = node.body[1:] if doc is not None else node.body
    return len(body) == 1 and isinstance(body[0], ast.Pass) and not _is_a_reason(doc)


def empty_downgrade(source: str) -> bool:
    """Whether ``source``'s ``downgrade()`` does nothing and gives no reason.

    Used by the visitor below, and by the test that keeps the migrations
    already on main from drifting back into the shape this rule catches.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            return _downgrade_is_empty(node)
    return False


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
        fk_targets: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.base_tables = base_tables
        self.new_tables = new_tables
        self.facts = facts
        self.findings: list[Finding] = []
        self._autocommit = 0
        self._not_valid_added = False
        self._unblocked_validates: list[ast.AST] = []
        self.constants: dict[str, ast.AST] = {}
        self._docstrings: set[int] = set()
        self.fk_targets = fk_targets or {}
        # The tables `with_lock_retries` names, and each table a foreign key
        # added or dropped in the migration's own transaction touches.
        self._locked: set[str] | None = None
        self._fk_tables: list[tuple[ast.AST, str, frozenset[str]]] = []
        # Keys this migration has created so far, by name: a later drop of the
        # same name drops this one, not the one history knows.
        self._created_here: dict[str, str] = {}
        self._helpers: dict[str, ast.FunctionDef] = {}
        self._helpers_visited: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self.constants = _module_constants(node)
        for scope in ast.walk(node):
            if isinstance(scope, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                body = scope.body
                if body and isinstance(body[0], ast.Expr) and _str(body[0].value) is not None:
                    self._docstrings.add(id(body[0].value))
        # Statements are judged in the order they run, not the order they are
        # written: a helper defined above upgrade() runs where upgrade() calls
        # it, after a with_lock_retries that comes earlier in upgrade().
        self._helpers = {
            stmt.name: stmt for stmt in node.body if isinstance(stmt, ast.FunctionDef) and stmt.name != "upgrade"
        }
        for stmt in node.body:
            if not (isinstance(stmt, ast.FunctionDef) and stmt.name in self._helpers):
                self.visit(stmt)
        # The rest never run under upgrade()'s lock (a downgrade's helpers):
        # judged for every other rule, but holding no with_lock_retries list.
        locked = self._locked
        for name, helper in self._helpers.items():
            if name not in self._helpers_visited:
                self._helpers_visited.add(name)
                self._locked = None  # each starts holding nothing
                self.visit(helper)
        self._locked = locked

    # -- plumbing --------------------------------------------------------

    def report(self, node: ast.AST, rule: str, message: str) -> None:
        finding = Finding(self.path, getattr(node, "lineno", 1), rule, message)
        if finding not in self.findings:  # a loop judged once per row says it once
            self.findings.append(finding)

    def existing(self, table: str | None) -> bool:
        return table is not None and table not in self.new_tables

    def _existing(self, node: ast.AST, table: str | None) -> bool:
        """``existing()``, but a name it cannot read is reported, not passed.

        A table the migration creates is exempt; one whose name is not a
        literal is not — the check cannot clear it, so it fails until spelled
        out or waived with a reason, the same as an unreadable drop.
        """
        if table is None:
            self.report(
                node,
                "unresolved-table",
                "could not read the table name; spell it as a literal (or justify an exception)",
            )
            return False
        return table not in self.new_tables

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "downgrade":
            # Its statements are not judged (see the module docstring); only an
            # empty one that does not say why is reported, then we stop before
            # descending into the body.
            if _downgrade_is_empty(node):
                self.report(
                    node,
                    "empty-downgrade",
                    "downgrade() is a lone `pass` with no reason; say in a docstring what it "
                    "deliberately leaves in place, or raise "
                    "(the Alembic `Downgrade schema.` template is not a reason)",
                )
            return
        self.generic_visit(node)

    def _table_names(self, node: ast.AST | None) -> list[str] | None:
        """The table an argument names (a literal or a module constant),
        ``schema.table`` read as ``table``."""
        name = _sql_text(node, self.constants)
        if name is None or HOLE in name:
            return None
        return [name.rsplit(".", 1)[-1]]

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
        if isinstance(func, ast.Name) and func.id == "with_lock_retries":
            tables = _sql_text(node.args[0] if node.args else None, self.constants)
            if tables is None or HOLE in tables:
                self.report(node, "unresolved", "could not read the tables with_lock_retries locks; check them by hand")
            else:
                locked = {t.strip().strip('"').rsplit(".", 1)[-1] for t in tables.split(",")}
                self._locked = (self._locked or set()) | locked
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "op":
            handler = getattr(self, f"_op_{func.attr}", None)
            if handler is not None:
                handler(node)
        self.generic_visit(node)
        if isinstance(func, ast.Name) and func.id in self._helpers and func.id not in self._helpers_visited:
            self._helpers_visited.add(func.id)
            self.visit(self._helpers[func.id])

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

    def _renamed(self, old: str | None, new: str | None) -> None:
        """A locked table keeps its lock under its new name."""
        if self._locked is not None and old in self._locked and new:
            self._locked.add(new)

    def _op_rename_table(self, node: ast.Call) -> None:
        self._renamed(_str(node.args[0]) if node.args else None, _str(node.args[1]) if len(node.args) > 1 else None)
        self._gone_from_base(node, "drop-table", _str(node.args[0]) if node.args else None, None)
        if self.findings and self.findings[-1].rule == "drop-table" and self.findings[-1].line == node.lineno:
            self.findings[-1].rule = "rename"

    def _op_alter_column(self, node: ast.Call) -> None:
        table = _str(node.args[0]) if node.args else None
        column = _str(node.args[1]) if len(node.args) > 1 else None
        if _kw(node, "new_column_name") is not None:
            self._gone_from_base(node, "rename", table, column)
        rewrites = _kw(node, "type_") is not None or (
            isinstance(_kw(node, "nullable"), ast.Constant) and _kw(node, "nullable").value is False  # type: ignore[union-attr]
        )
        if rewrites and self._existing(node, table):
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
        concurrently = _truthy(_kw(node, "postgresql_concurrently"))
        if table is None and concurrently and self._autocommit:
            # Concurrent, in its own transaction: safe on an existing table and
            # on one this migration creates, so the name being unreadable costs
            # nothing.
            return
        if not self._existing(node, table):
            return
        if not concurrently:
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

    def _fk_touches(self, node: ast.AST, *tables: str | None) -> None:
        # Only a lock taken after with_lock_retries holds the listed tables.
        if self._locked is not None and not self._autocommit:
            self._fk_tables.extend((node, t, frozenset(self._locked)) for t in tables if t)

    def _op_drop_constraint(self, node: ast.Call) -> None:
        name = _sql_text(node.args[0] if node.args else _kw(node, "constraint_name"), self.constants)
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        kind = _str(node.args[2]) if len(node.args) > 2 else _str(_kw(node, "type_"))
        self._fk_dropped(node, table, name, kind == "foreignkey")

    def _fk_dropped(self, node: ast.AST, table: str | None, name: str | None, is_fk: bool) -> None:
        """A key dropped by name locks the table it pointed at just before the
        drop: as this migration last created it, else as its ancestors last did."""
        target = self._created_here.get(name or "") or self.fk_targets.get(name or "")
        if target:
            self._fk_touches(node, table, target)
            return
        looks_like_fk = is_fk or bool(name and (name.endswith("_fkey") or name.startswith("fk_")))
        if looks_like_fk and self._locked is not None and not self._autocommit:
            self.report(
                node,
                "unresolved",
                f"could not tell which table foreign key {name} points at; check that with_lock_retries names it",
            )

    def _fk_created(self, node: ast.AST, table: str | None, keys: list[tuple[str, str]]) -> None:
        for name, ref in keys:
            self._created_here[name] = ref
        if keys:
            self._fk_touches(node, table, *(ref for _, ref in keys))

    def _op_create_foreign_key(self, node: ast.Call) -> None:
        source = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "source_table"))
        # Locked whatever the key is called (None, an f-string in a loop); only
        # a readable name can be remembered for a later drop.
        sources = self._table_names(node.args[1] if len(node.args) > 1 else _kw(node, "source_table"))
        referents = self._table_names(node.args[2] if len(node.args) > 2 else _kw(node, "referent_table"))
        self._fk_touches(node, *(sources or []), *(referents or []))
        if (sources is None or referents is None) and self._locked is not None and not self._autocommit:
            self.report(
                node,
                "unresolved",
                "could not read this foreign key's tables; check that with_lock_retries names them",
            )
        for name, ref in _created_key(node, self.constants):
            self._created_here[name] = ref
        not_valid = _truthy(_kw(node, "postgresql_not_valid"))
        if not_valid:
            self._not_valid_added = True
        elif self._existing(node, source):
            self.report(
                node,
                "constraint-not-valid",
                f"foreign key on existing table {source} without postgresql_not_valid=True scans it under lock; "
                "add it NOT VALID, then VALIDATE CONSTRAINT",
            )

    def _op_create_check_constraint(self, node: ast.Call) -> None:
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        not_valid = _truthy(_kw(node, "postgresql_not_valid"))
        if not_valid:
            self._not_valid_added = True
        elif self._existing(node, table):
            self.report(
                node,
                "constraint-not-valid",
                f"CHECK on existing table {table} without postgresql_not_valid=True scans it under lock; "
                "add it NOT VALID, then VALIDATE CONSTRAINT",
            )

    def _op_create_unique_constraint(self, node: ast.Call) -> None:
        table = _str(node.args[1]) if len(node.args) > 1 else _str(_kw(node, "table_name"))
        if self._existing(node, table):
            self.report(
                node,
                "unique-constraint",
                f"UNIQUE constraint on existing table {table} builds its index under lock; "
                "CREATE UNIQUE INDEX CONCURRENTLY, then ADD CONSTRAINT … USING INDEX",
            )

    def _op_add_column(self, node: ast.Call) -> None:
        table = (self._table_names(node.args[0]) or [None])[0] if node.args else None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Call):
            keys = _inline_keys(table, node.args[1], self.constants)
            self._fk_created(node, table, keys)
            if keys and table is None and self._locked is not None and not self._autocommit:
                self.report(
                    node,
                    "unresolved",
                    "could not read the table this foreign key's column is added to; "
                    "check that with_lock_retries names it",
                )
        column = _column(node.args[1]) if len(node.args) > 1 and isinstance(node.args[1], ast.Call) else None
        if column is None:
            return
        if table is None:
            # Only the existing-table rules need the name; report the one that
            # would fire had it been read (a nullable column harms nothing).
            if not column.nullable and not column.server_default:
                self.report(
                    node,
                    "unresolved-table",
                    f"NOT NULL column {column.name} on a table whose name could not be read; "
                    "spell the table as a literal (or justify an exception)",
                )
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
        for arg in node.args[1:]:
            if isinstance(arg, ast.Call):
                # The new table itself is exempt (new_tables); its targets are not.
                self._fk_created(node, None, _inline_keys(table, arg, self.constants))
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
                    new_name = re.search(r"\bTO\s+\"?(\w+)", clause, re.I)
                    self._renamed(table, new_name.group(1) if new_name else None)
                    self._gone_from_base(node, "drop-table", table, None)
                    if self.findings and self.findings[-1].line == node.lineno and self.findings[-1].rule == "drop-table":
                        self.findings[-1].rule = "rename"
                    continue
                if (column := _SQL_RENAME_COLUMN.search(clause)) is not None:
                    self._gone_from_base(node, "rename", table, column.group(1))
                    continue
                if (dropped := _SQL_DROP_CONSTRAINT.match(clause)) is not None:
                    self._fk_dropped(node, table, dropped.group(1), False)
                    continue
                if (referenced := _SQL_REFERENCES.search(clause)) is not None:
                    named = _SQL_NAMED_FK.search(clause)
                    self._fk_touches(node, table, referenced.group(1))
                    if named:
                        self._created_here[named.group(1)] = named.group(2)
                if _SQL_VALIDATE_CONSTRAINT.search(clause):
                    if not self._autocommit and node not in self._unblocked_validates:
                        self._unblocked_validates.append(node)
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
                if constraint is not None and re.search(r"\bNOT\s+VALID\b", clause, re.I):
                    self._not_valid_added = True
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
            self._fk_touches(node, *_SQL_REFERENCES.findall(sql))

    def finish(self) -> None:
        """Rules that need the whole file, not one statement: a ``NOT VALID``
        constraint and its ``VALIDATE`` are safe only when the VALIDATE runs in
        its own transaction. In the migration's own, it holds the lock to the
        end of the migration and cancels what ``NOT VALID`` bought. And a
        foreign key's tables are judged against the ``with_lock_retries`` list
        held when it ran."""
        for node, table, locked in self._fk_tables:
            if table not in locked and table not in self.new_tables:
                self.report(
                    node,
                    "fk-table-not-locked",
                    f"this foreign key locks {table}, which with_lock_retries does not name; "
                    "taken after the listed tables, it deadlocks with the live backend - add it to the list",
                )
        if not self._not_valid_added:
            return
        for node in self._unblocked_validates:
            self.report(
                node,
                "validate-outside-autocommit",
                "VALIDATE CONSTRAINT shares the transaction with the NOT VALID constraint it follows, "
                "holding the lock to the end of the migration; "
                "put it in `with op.get_context().autocommit_block():`",
            )


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
    """Tables a migration creates (``op.create_table`` or ``CREATE TABLE``),
    literal loops written out."""
    out: set[str] = set()
    try:
        tree = _unrolled(ast.parse(source))
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


_SQL_NAMED_FK = re.compile(
    r"\bCONSTRAINT\s+\"?(\w+)\"?\s+FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+(?:\"?\w+\"?\.)?\"?(\w+)", re.I
)
_SQL_REFERENCES = re.compile(r"\bREFERENCES\s+(?:\"?\w+\"?\.)?\"?(\w+)", re.I)
_SQL_DROP_CONSTRAINT = re.compile(r"^\s*DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?\"?(\w+)", re.I)


def _fk_referent(call: ast.Call) -> str | None:
    """``"topics"`` for ``sa.ForeignKey("topics.id")``; ``ForeignKeyConstraint``'s first target."""
    target = call.args[0] if _callee(call) == "ForeignKey" and call.args else None
    if _callee(call) == "ForeignKeyConstraint" and len(call.args) > 1:
        refs = call.args[1]
        target = refs.elts[0] if isinstance(refs, ast.List | ast.Tuple) and refs.elts else None
    spelled = _str(target)
    return spelled.rsplit(".", 2)[-2] if spelled and "." in spelled else None


class _Bind(ast.NodeTransformer):
    """Replace loop variables with one row's values, folding an f-string whose
    every part became a literal into that literal."""

    def __init__(self, values: dict[str, ast.AST]) -> None:
        self.values = values

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.values:
            return ast.copy_location(copy.deepcopy(self.values[node.id]), node)
        return node

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        self.generic_visit(node)
        parts = []
        for value in node.values:
            if _str(value) is not None:
                parts.append(_str(value))
            elif (
                isinstance(value, ast.FormattedValue)
                and value.conversion == -1
                and value.format_spec is None
                and _str(value.value) is not None
            ):
                parts.append(_str(value.value))
            else:
                return node
        return ast.copy_location(ast.Constant("".join(parts)), node)  # type: ignore[arg-type]


class _Unroll(ast.NodeTransformer):
    """Write out every loop over literal rows (a tuple or list, or a module
    constant holding one; unpacked or not): its body once per row, each loop
    variable replaced by that row's value, then its ``else``. Every rule, and
    the reading of earlier migrations' keys, then sees the names the written-out
    form names. A loop stays as it is when its targets are not plain names, its
    rows do not match them, or its body skips a row (``continue`` / ``break``),
    branches on a loop variable or rebinds one. An inner loop is written out
    after the outer row is bound, so it can iterate over an outer variable."""

    def __init__(self, constants: dict[str, ast.AST]) -> None:
        self.constants = constants

    def _visit_all(self, stmts: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for stmt in stmts:
            result = self.visit(stmt)
            out += result if isinstance(result, list) else [result]
        return out

    def visit_For(self, node: ast.For) -> ast.AST | list[ast.stmt]:
        rows = _literal_rows(node.iter, self.constants)
        targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if (
            rows is None
            or len(names) != len(targets)
            or any(len(row) != len(targets) for row in rows)
            or any(_rebinds_or_branches(n, names) for stmt in node.body for n in ast.walk(stmt))
        ):
            self.generic_visit(node)
            return node
        out: list[ast.stmt] = []
        for row in rows:
            bind = _Bind({t.id: value for t, value in zip(targets, row, strict=True)})  # type: ignore[attr-defined]
            out += self._visit_all([bind.visit(copy.deepcopy(stmt)) for stmt in node.body])
        return out + self._visit_all(node.orelse)


def _rebinds_or_branches(node: ast.AST, names: set[str]) -> bool:
    """Whether written out, the body would not be the same for every row: it
    rebinds a loop variable (any binding form), skips rows (``continue`` /
    ``break``), or branches on a loop variable."""
    if isinstance(node, ast.Continue | ast.Break):
        return True
    if isinstance(node, ast.If | ast.IfExp | ast.While | ast.Match):
        test = node.subject if isinstance(node, ast.Match) else node.test
        return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(test))
    bound: set[str] = set()
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        bound = {node.id}
    elif isinstance(node, ast.arg):
        bound = {node.arg}
    elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
        bound = {node.name}
    return bool(bound & names)


def _unrolled(tree: ast.Module) -> ast.Module:
    tree = _Unroll(_module_constants(tree)).visit(tree)
    return ast.fix_missing_locations(tree)


def _literal_rows(node: ast.AST | None, constants: dict[str, ast.AST]) -> list[list[ast.AST]] | None:
    """The items of a literal tuple/list (a module constant included), each as
    a row: a tuple item is its elements, anything else a row of one."""
    if isinstance(node, ast.Name) and node.id in constants:
        node = constants[node.id]
    if not isinstance(node, ast.Tuple | ast.List):
        return None
    return [list(item.elts) if isinstance(item, ast.Tuple | ast.List) else [item] for item in node.elts]


def _inline_keys(table: str | None, call: ast.Call, constants: dict[str, ast.AST]) -> list[tuple[str, str]]:
    """``(name, referenced table)`` for each key a ``Column(..., ForeignKey(...))``
    or ``ForeignKeyConstraint`` declares. An unnamed key gets Postgres' default
    name, ``<table>_<column>_fkey``."""
    if _callee(call) == "Column" and call.args:
        column = _str(call.args[0])
        keys = [a for a in call.args[1:] if isinstance(a, ast.Call) and _callee(a) == "ForeignKey"]
    elif _callee(call) == "ForeignKeyConstraint" and call.args:
        column, keys = _leading_column(call.args[0]), [call]
    else:
        return []
    out = []
    for fk in keys:
        ref = _fk_referent(fk)
        if ref:
            out.append((_sql_text(_kw(fk, "name"), constants) or f"{table}_{column}_fkey", ref))
    return out


def _created_key(call: ast.Call, constants: dict[str, ast.AST]) -> list[tuple[str, str]]:
    """``op.create_foreign_key(name, source, referent, ...)`` as ``[(name, referent)]``."""
    name = _sql_text(call.args[0] if call.args else _kw(call, "constraint_name"), constants)
    ref = _sql_text(call.args[2] if len(call.args) > 2 else _kw(call, "referent_table"), constants)
    return [(name, ref.rsplit(".", 1)[-1])] if name and ref and HOLE not in name and HOLE not in ref else []


@dataclass
class _Creations:
    revision: str | None
    down: list[str]
    keys: list[tuple[str, str]]


def fk_creations(sources: dict[str, str]) -> dict[str, _Creations]:
    """For each migration: its revision, its parents, and the foreign keys its
    ``upgrade()`` creates, in the order it creates them."""
    out: dict[str, _Creations] = {}
    for path, source in sources.items():
        try:
            tree = _unrolled(ast.parse(source))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                node.body = []
        constants = _module_constants(tree)
        down = constants.get("down_revision")
        parents = [_str(e) for e in down.elts] if isinstance(down, ast.Tuple | ast.List) else [_str(down)]
        keys: list[tuple[int, tuple[str, str]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node)
            found: list[tuple[str, str]] = []
            if callee == "create_foreign_key":
                found = _created_key(node, constants)
            elif callee in {"create_table", "add_column"} and node.args and _str(node.args[0]):
                for arg in node.args[1:]:
                    if isinstance(arg, ast.Call):
                        found += _inline_keys(_str(node.args[0]), arg, constants)
            elif callee == "execute" and node.args:
                sql = _strip_sql_comments(_sql_text(node.args[0], constants) or "")
                found = _SQL_NAMED_FK.findall(sql)
            keys += [(node.lineno, key) for key in found]
        out[path] = _Creations(
            _str(constants.get("revision")), [p for p in parents if p], [key for _, key in sorted(keys)]
        )
    return out


def foreign_key_targets(creations: dict[str, _Creations], path: str) -> dict[str, str]:
    """``{constraint name: referenced table}`` as the migrations before ``path``
    left them: its ancestors, walked parents first, the latest creation of a
    name winning. A migration whose parents are not among ``creations`` (a
    fragment, not a chain) is preceded by every other one, in path order."""
    by_revision: dict[str, list[str]] = {}
    for other, info in creations.items():
        if info.revision:
            by_revision.setdefault(info.revision, []).append(other)
    current = creations.get(path)
    chained = current is not None and any(p in by_revision for p in current.down)
    order: list[str] = []
    placed: set[str] = set()
    pending = [(other, False) for other in reversed(sorted(creations))] if not chained else []
    if chained:
        pending = [(other, False) for p in reversed(current.down) for other in by_revision.get(p, [])]
    while pending:
        other, expanded = pending.pop()
        if other in placed or other == path:
            continue
        if expanded:
            placed.add(other)
            order.append(other)
            continue
        pending.append((other, True))
        for parent in creations[other].down:
            pending.extend((p, False) for p in by_revision.get(parent, []) if p not in placed)
    out: dict[str, str] = {}
    for other in order:
        out.update(creations[other].keys)
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


def analyze(
    migrations: dict[str, str], base_models: dict[str, str], history: dict[str, str] | None = None
) -> list[Finding]:
    """Findings for the given migration sources, judged against ``base_models``.

    ``history``: the migrations already on the base, read only to learn which
    table a foreign key dropped by name points at."""
    base_tables = read_models(base_models)
    creations = fk_creations({**(history or {}), **migrations})
    new_tables: set[str] = set()
    for source in migrations.values():
        new_tables |= created_tables(source)
    findings: list[Finding] = []
    indexed_anywhere: set[tuple[str, str]] = set()
    pending: list[tuple[list[str], dict[int, tuple[int, int]], list[Finding], _FileFacts]] = []
    for path, source in sorted(migrations.items()):
        try:
            tree = _unrolled(ast.parse(source))
        except SyntaxError as error:
            # A file that does not parse was not read and cleared: every statement
            # in it is unjudged. `unresolved` is a warning for one statement whose
            # SQL is dynamic, so reusing it here let a broken file exit 0.
            findings.append(Finding(path, error.lineno or 1, "unparseable", f"does not parse: {error}"))
            continue
        facts = _FileFacts()
        visitor = _Migration(path, source, base_tables, new_tables, facts, foreign_key_targets(creations, path))
        visitor.visit(tree)
        visitor.finish()
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
            if finding not in findings and not _allowed(lines, finding, spans.get(finding.line, (finding.line, finding.line))):
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


def cannot_judge(base: str) -> int:
    """Refuse to answer when ``base`` cannot be read, instead of passing by default.

    Which migrations are new, and the models the release still serving maps, both
    come from ``base``: run without it and the drop/rename rules have nothing to
    compare against, so a clean report would mean "nothing looked at", not "nothing
    wrong". The explanation goes to stderr, the way check-file-sizes.py reports its
    own 2, so that stdout carries a verdict or nothing.
    """
    print(
        f"cannot judge: {base} is not a ref this checkout has, so the migrations this "
        "branch adds cannot be told from the ones already there.",
        file=sys.stderr,
    )
    print("Fetch it first (`git fetch --unshallow origin`), or name another --base.", file=sys.stderr)
    return EXIT_CANNOT_JUDGE


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
        history = read_tree(args.base, args.versions_dir)
        paths = files or added_migrations(args.base, args.versions_dir)
    except subprocess.CalledProcessError:
        return cannot_judge(args.base)

    if not paths:
        print(f"PASS: no migrations added relative to {args.base}")
        return 0

    # `utf-8-sig` drops a BOM: Python's file reader tolerates one, but `ast.parse`
    # on the decoded text rejects U+FEFF, which would now block a valid migration.
    migrations = {p: Path(p).read_text(encoding="utf-8-sig") for p in paths if Path(p).is_file()}
    findings = analyze(migrations, base_models, history)
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


def _downgrade_rules(body: str) -> list[str]:
    """The rules a source fires whose ``downgrade()`` body is ``body``."""
    source = _HEAD + "\ndef upgrade() -> None:\n    pass\n\ndef downgrade() -> None:\n" + body + "\n"
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
            "    with op.get_context().autocommit_block():\n"
            '        op.execute("ALTER TABLE task VALIDATE CONSTRAINT fk")\n'
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
        "a clause from a loop over literals is judged as written out",
        _rules_found('    for clause in ("DROP COLUMN video_url",):\n        op.execute(f"ALTER TABLE task {clause}")'),
        ["drop-column"],
    )
    check(
        "a hole where the clause goes is reported",
        _rules_found('    for clause in clauses():\n        op.execute(f"ALTER TABLE task {clause}")'),
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

    # --- a foreign key's tables are in the with_lock_retries list ------------
    # b8d3f1a6c254 locked conversations first, then dropped a key pointing at
    # topics: the late topics lock deadlocked with the live backend.
    add_fk = "    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)"
    check(
        "a new foreign key's target left out of the lock list",
        _rules_found("    with_lock_retries('task')\n" + add_fk),
        ["fk-table-not-locked"],
    )
    check("a new foreign key with both tables locked", _rules_found("    with_lock_retries('task, users')\n" + add_fk), [])
    check("a new foreign key with no lock list at all", _rules_found(add_fk), [])
    check(
        "a foreign key added in its own autocommit transaction",
        _rules_found("    with_lock_retries('task')\n    with op.get_context().autocommit_block():\n    " + add_fk),
        [],
    )
    check(
        "raw SQL: a REFERENCES target left out of the lock list",
        _rules_found(
            "    with_lock_retries('task')\n"
            '    op.execute("ALTER TABLE task ADD CONSTRAINT fk FOREIGN KEY (owner_id) REFERENCES users (id) NOT VALID")'
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a table renamed under its lock keeps it",
        _rules_found(
            "    with_lock_retries('legacy, users')\n"
            "    op.rename_table('legacy', 'fresh_name')  # migration-safety: allow rename — self-test\n"
            "    op.create_foreign_key('fk', 'fresh_name', 'users', ['u'], ['id'], postgresql_not_valid=True)"
        ),
        [],
    )
    created = {"a.py": _HEAD + "def upgrade():\n    op.create_table('runs', sa.Column('topic_id', sa.Integer(), sa.ForeignKey('topics.id')))\n"}
    drop_fk = "    op.drop_constraint('runs_topic_id_fkey', 'runs', type_='foreignkey')"

    def dropped(lock: str) -> list[str]:
        source = _HEAD + f"\ndef upgrade() -> None:\n    with_lock_retries('{lock}')\n{drop_fk}\n"
        return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS, created))

    check("a dropped key's target, read from the migration that made it, left out", dropped("runs"), ["fk-table-not-locked"])
    check("a dropped key's target locked", dropped("runs, topics"), [])
    check("a dropped key nothing says the target of", _rules_found("    with_lock_retries('runs')\n" + drop_fk), ["unresolved"])

    def judged(body: str, history: dict[str, str]) -> list[str]:
        source = _HEAD + "\ndef upgrade() -> None:\n" + body + "\n"
        return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS, history))

    raw_drop = '    op.execute("ALTER TABLE runs DROP CONSTRAINT runs_topic_id_fkey")'
    check("raw SQL: a dropped key's target left out", judged("    with_lock_retries('runs')\n" + raw_drop, created), ["fk-table-not-locked"])
    check("raw SQL: a dropped key's target locked", judged("    with_lock_retries('runs, topics')\n" + raw_drop, created), [])
    repointed = {
        **created,
        "b.py": _HEAD + "def upgrade():\n"
        "    op.create_foreign_key('runs_topic_id_fkey', 'runs', 'conversations', ['topic_id'], ['id'])\n",
    }
    check(
        "a key re-pointed earlier is dropped from where it last pointed",
        judged("    with_lock_retries('runs, conversations')\n" + drop_fk, repointed),
        [],
    )

    # The chain, not the file name, says which creation came last.
    def rev(revision: str, down: str | tuple[str, ...] | None) -> str:
        return f"import sqlalchemy as sa\nfrom alembic import op\nrevision = {revision!r}\ndown_revision = {down!r}\n"

    chained = {
        "z.py": rev("r1", None) + "def upgrade():\n"
        "    op.create_table('runs', sa.Column('topic_id', sa.Integer(), sa.ForeignKey('topics.id')))\n",
        "a.py": rev("r2", "r1") + "def upgrade():\n"
        "    op.create_foreign_key('runs_topic_id_fkey', 'runs', 'conversations', ['topic_id'], ['id'])\n",
    }

    def chained_rules(lock: str) -> list[str]:
        source = rev("r3", "r2") + f"def upgrade():\n    with_lock_retries('{lock}')\n{drop_fk}\n"
        return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS, chained))

    check("a re-pointed key follows the chain, not file names", chained_rules("runs, conversations"), [])
    check("... and its old target is not enough", chained_rules("runs, topics"), ["fk-table-not-locked"])
    chained["later.py"] = (
        rev("r4", "r3") + "def upgrade():\n"
        "    op.create_foreign_key('runs_topic_id_fkey', 'runs', 'elsewhere', ['topic_id'], ['id'])\n"
    )
    check("... and a later migration's re-point is not seen yet", chained_rules("runs, conversations"), [])

    def merged_rules(lock: str) -> list[str]:
        source = rev("r3", ("r2", "r1b")) + f"def upgrade():\n    with_lock_retries('{lock}')\n{drop_fk}\n"
        return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS, {**chained, "y.py": rev("r1b", None)}))

    check("a merge migration reads its keys through every parent", merged_rules("runs, conversations"), [])

    def second_parent_rules(lock: str) -> list[str]:
        source = rev("r3", ("r2", "r1b")) + f"def upgrade():\n    with_lock_retries('{lock}')\n" + (
            "    op.drop_constraint('runs_owner_fkey', 'runs', type_='foreignkey')\n"
        )
        y = rev("r1b", None) + (
            "def upgrade():\n    op.create_foreign_key('runs_owner_fkey', 'runs', 'users', ['u'], ['id'])\n"
        )
        return sorted(f.rule for f in analyze({"m.py": source}, _BASE_MODELS, {**chained, "y.py": y}))

    check("... including a key only the second parent made", second_parent_rules("runs"), ["fk-table-not-locked"])
    check(
        "a key taken before a later with_lock_retries is judged by the list held then",
        _rules_found(
            "    with_lock_retries('task')\n" + add_fk + "\n    with_lock_retries('users')"
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a schema-qualified referent is the table it names",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    op.create_foreign_key('fk', 'task', 'public.users', ['owner_id'], ['id'], postgresql_not_valid=True)"
        ),
        [],
    )
    check(
        "a key whose referent is a local variable or an f-string is not passed silently",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    ref = 'users'\n"
            "    op.create_foreign_key(None, 'task', ref, ['u'], ['id'], postgresql_not_valid=True)\n"
            "    op.create_foreign_key(None, 'task', f'{ref}', ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["unresolved", "unresolved"],
    )
    two_helpers = (
        "def _drop_task_fk():\n    with_lock_retries('task, users')\n"
        "    op.drop_constraint('ck_task_kind', 'task', type_='check')\n"
        "def _restore_runs_fk():\n"
        "    op.create_foreign_key('runs_fk', 'runs', 'users', ['u'], ['id'], postgresql_not_valid=True)\n"
    )
    check(
        "downgrade helpers do not inherit each other's lock list",
        sorted(
            f.rule
            for f in analyze(
                {
                    "m.py": _HEAD + two_helpers + "\ndef upgrade() -> None:\n    pass\n"
                    "\ndef downgrade() -> None:\n    _drop_task_fk()\n    _restore_runs_fk()\n"
                },
                _BASE_MODELS,
            )
        ),
        [],
    )
    check(
        "a key whose name is not a literal is still judged",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    for table in ('task',):\n"
            "        op.create_foreign_key(f'fk_{table}_owner_id', table, 'users', ['owner_id'], ['id'],"
            " postgresql_not_valid=True)\n"
            "    op.create_foreign_key(None, 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)"
        ),
        ["fk-table-not-locked", "fk-table-not-locked"],
    )
    check(
        "a key whose source table is a loop variable over literals",
        _rules_found(
            "    with_lock_retries('conversations')\n"
            "    for table in ('tasks', 'documents'):\n"
            "        op.create_foreign_key(f'fk_{table}_c', table, 'conversations', ['c'], ['id'], postgresql_not_valid=True)"
        ),
        ["fk-table-not-locked", "fk-table-not-locked"],
    )
    check(
        "a key whose referent is a loop variable, unpacked from a module constant",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    for column, ref in _REFS:\n"
            "        op.create_foreign_key(None, 'task', ref, [column], ['id'], postgresql_not_valid=True)",
            "_REFS = (('owner_id', 'users'), ('team_id', 'team'))\n",
        ),
        ["fk-table-not-locked", "fk-table-not-locked"],
    )
    check(
        "a key whose tables cannot be read is not passed silently",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    for table in _tables():\n"
            "        op.create_foreign_key(None, table, 'users', ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["fk-table-not-locked", "unresolved"],
    )
    check(
        "a key this migration drops and re-adds under its name was locked at its old target",
        judged(
            "    with_lock_retries('runs, conversations')\n" + drop_fk + "\n"
            "    op.create_foreign_key('runs_topic_id_fkey', 'runs', 'conversations', ['topic_id'], ['id'],"
            " postgresql_not_valid=True)",
            created,
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a key this migration created, then dropped, points where it created it",
        _rules_found(
            "    with_lock_retries('runs, users')\n"
            "    op.create_foreign_key('runs_owner_fkey', 'runs', 'users', ['u'], ['id'], postgresql_not_valid=True)\n"
            "    op.drop_constraint('runs_owner_fkey', 'runs', type_='foreignkey')"
        ),
        [],
    )
    helper = "def _move():\n    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)\n"
    check(
        "a helper defined above upgrade() runs where upgrade() calls it",
        _rules_found("    with_lock_retries('task')\n    _move()", helper),
        ["fk-table-not-locked"],
    )
    check(
        "a helper only downgrade() calls holds no lock",
        sorted(
            f.rule
            for f in analyze(
                {
                    "m.py": _HEAD + helper + "\ndef upgrade() -> None:\n    with_lock_retries('task')\n"
                    "\ndef downgrade() -> None:\n    _move()\n"
                },
                _BASE_MODELS,
            )
        ),
        [],
    )
    own_lock = (
        "def _undo():\n    with_lock_retries('task, users')\n"
        "    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)\n"
    )
    check(
        "a downgrade helper is judged against its own lock list, not upgrade()'s",
        sorted(
            f.rule
            for f in analyze(
                {
                    "m.py": _HEAD + own_lock + "\ndef upgrade() -> None:\n    with_lock_retries('runs')\n"
                    "\ndef downgrade() -> None:\n    _undo()\n"
                },
                _BASE_MODELS,
            )
        ),
        [],
    )
    check(
        "a key added with its column, the column's table left out",
        _rules_found(
            "    with_lock_retries('users')\n"
            "    op.add_column('task', sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), index=True))"
        ),
        ["fk-on-add-column", "fk-table-not-locked"],
    )
    restored_in_downgrade = {
        **created,
        "c.py": _HEAD + "def upgrade():\n    pass\ndef downgrade():\n"
        "    op.create_foreign_key('runs_topic_id_fkey', 'runs', 'elsewhere', ['topic_id'], ['id'])\n",
    }
    check(
        "a downgrade's keys do not count as created",
        judged("    with_lock_retries('runs, topics')\n" + drop_fk, restored_in_downgrade),
        [],
    )
    check(
        "a key added inline with its column, target left out",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    op.add_column('task', sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))"
        ),
        ["fk-on-add-column", "fk-table-not-locked", "fk-without-index"],
    )
    check(
        "a new table's inline key, target left out",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    op.create_table('fresh', sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), index=True))"
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a new table's ForeignKeyConstraint, target left out",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    op.create_table('fresh', sa.Column('owner_id', sa.Integer()),"
            " sa.ForeignKeyConstraint(['owner_id'], ['users.id']), sa.Index('ix_fresh_owner_id', 'owner_id'))"
        ),
        ["fk-table-not-locked"],
    )
    check(
        "raw SQL: a new table's REFERENCES target left out",
        _rules_found(
            "    with_lock_retries('task')\n"
            '    op.execute("CREATE TABLE fresh (id int PRIMARY KEY, owner_id int REFERENCES users (id))")'
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a key added before with_lock_retries holds nothing yet",
        _rules_found(add_fk + "\n    with_lock_retries('task')"),
        [],
    )
    check(
        "raw SQL: a dropped key nothing says the target of",
        _rules_found('    with_lock_retries(\'runs\')\n    op.execute("ALTER TABLE runs DROP CONSTRAINT mystery_fkey")'),
        ["unresolved"],
    )
    check(
        "raw SQL: a dropped constraint that is not named like a key",
        _rules_found('    with_lock_retries(\'runs\')\n    op.execute("ALTER TABLE runs DROP CONSTRAINT ck_runs_kind")'),
        [],
    )
    two_targets = {
        "a.py": _HEAD + "def upgrade():\n    op.create_foreign_key('owner_link', 'runs', 'users', ['u'], ['id'])\n",
        "b.py": _HEAD + "def upgrade():\n    op.create_foreign_key('owner_link', 'runs', 'teams', ['u'], ['id'])\n",
    }
    check(
        "raw SQL: a key with an unremarkable name is read from history",
        judged("    with_lock_retries('runs, users')\n    op.execute(\"ALTER TABLE runs DROP CONSTRAINT owner_link\")", two_targets),
        ["fk-table-not-locked"],
    )
    check("a dropped unknown key with no lock list", _rules_found(drop_fk), [])
    check(
        "a dropped unknown key in its own autocommit transaction",
        _rules_found("    with_lock_retries('runs')\n    with op.get_context().autocommit_block():\n    " + drop_fk),
        [],
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

    # Found in review (2026-10-07): a name no literal spells, and a NOT VALID
    # constraint validated in the migration's own transaction.
    check(
        "a unique constraint on each table a literal loop names",
        _rules_found(
            "    for table in ('tasks', 'topics'):\n"
            "        op.create_unique_constraint(f'uq_{table}_number', table, ['project_id', 'number'])"
        ),
        ["unique-constraint", "unique-constraint"],
    )
    check(
        "a unique constraint on a table an unreadable loop names",
        _rules_found(
            "    for table in tables():\n"
            "        op.create_unique_constraint(f'uq_{table}_number', table, ['project_id', 'number'])"
        ),
        ["unresolved-table"],
    )
    check(
        "an index on each table a literal loop names",
        _rules_found("    for t in ('task', 'tasks'):\n        op.create_index('ix', t, ['x'])"),
        ["index-not-concurrent", "index-not-concurrent"],
    )
    check(
        "a nullable column on a table only a loop variable names",
        _rules_found(
            "    for t in ('task',):\n        op.add_column(t, sa.Column('n', sa.Integer(), nullable=True))"
        ),
        [],
    )
    check(
        "a NOT NULL column on a table a literal loop names",
        _rules_found(
            "    for t in ('task',):\n        op.add_column(t, sa.Column('n', sa.Integer(), nullable=False))"
        ),
        ["add-column-not-null"],
    )
    check(
        "a NOT NULL column on a table an unreadable loop names",
        _rules_found(
            "    for t in tables():\n        op.add_column(t, sa.Column('n', sa.Integer(), nullable=False))"
        ),
        ["unresolved-table"],
    )
    check(
        "a key added with a column in a literal loop, its table left out",
        _rules_found(
            "    with_lock_retries('users')\n"
            "    for t in ('task',):\n"
            "        op.add_column(t, sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), index=True))"
        ),
        ["fk-on-add-column", "fk-table-not-locked"],
    )
    check(
        "a referent held in a module constant is the table it names",
        _rules_found(
            "    with_lock_retries('task')\n"
            "    op.create_foreign_key(None, 'task', USERS, ['u'], ['id'], postgresql_not_valid=True)\n"
            "    op.create_foreign_key(None, 'task', referent_table=USERS, source_table='task',"
            " local_cols=['u'], remote_cols=['id'], postgresql_not_valid=True)",
            "USERS = 'users'\n",
        ),
        ["fk-table-not-locked", "fk-table-not-locked"],
    )
    check(
        "a schema-qualified key dropped later is the table it names",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    op.create_foreign_key('fk', 'task', 'public.users', ['u'], ['id'], postgresql_not_valid=True)\n"
            "    op.drop_constraint('fk', 'task', type_='foreignkey')"
        ),
        [],
    )
    check(
        "a starred loop target is not unrolled",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    for *rest, ref in (('x', 'users'),):\n"
            "        op.create_foreign_key(None, 'task', ref, ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["unresolved"],
    )
    check(
        "a loop whose rows do not match its targets is not unrolled",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    for t, ref in (('task', 'users', 'extra'),):\n"
            "        op.create_foreign_key(None, t, ref, ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["unresolved"],
    )
    check(
        "a loop that skips rows is not unrolled",
        _rules_found(
            "    for t in ('task', 'legacy'):\n"
            "        if t == 'task':\n            continue\n"
            "        op.execute(f'ALTER TABLE {t} DROP COLUMN video_url')"
        ),
        ["unresolved"],
    )
    check(
        "a loop that may skip a row on another condition is not unrolled",
        _rules_found(
            "    for t in ('task',):\n"
            "        if ready():\n            continue\n"
            "        op.execute(f'ALTER TABLE {t} DROP COLUMN video_url')"
        ),
        ["unresolved"],
    )
    check(
        "a loop that rebinds its variable as a parameter is not unrolled",
        _rules_found(
            "    for t in ('task',):\n"
            "        f = lambda t: op.create_index('ix', t, ['x'])"
        ),
        ["unresolved-table"],
    )
    check(
        "a column added to a table held in a module constant",
        _rules_found(
            "    op.add_column(TASK, sa.Column('n', sa.Integer(), nullable=False))",
            "TASK = 'task'\n",
        ),
        ["add-column-not-null"],
    )
    check(
        "an inner loop over an outer row's value is written out too",
        _rules_found(
            "    for table, columns in (('task', ('video_url',)),):\n"
            "        for column in columns:\n"
            "            op.drop_column(table, column)"
        ),
        ["drop-column"],
    )
    check(
        "a table a literal loop creates counts as new",
        _rules_found(
            "    for t in ('alpha',):\n"
            "        op.create_table(t, sa.Column('id', sa.Integer(), primary_key=True))\n"
            "    op.create_index('ix_alpha_id', 'alpha', ['id'])"
        ),
        [],
    )
    check(
        "a literal loop's else runs once, after it",
        _rules_found("    for t in ('task',):\n        pass\n    else:\n        op.drop_column('task', 'video_url')"),
        ["drop-column"],
    )
    check(
        "a statement repeated by a loop is reported once",
        _rules_found("    for c in ('a', 'b'):\n        op.create_index('ix', 'task', [c])"),
        ["index-not-concurrent"],
    )
    check(
        "an f-string with a conversion is not folded into a name",
        _rules_found("    for t in ('task',):\n        op.execute(f'CREATE INDEX ix ON {t!r} (x)')"),
        ["unresolved"],
    )
    looped_history = {
        "z.py": rev("r1", None) + "def upgrade():\n"
        "    for t in ('runs',):\n"
        "        op.create_foreign_key(f'{t}_topic_id_fkey', t, 'topics', ['topic_id'], ['id'])\n",
    }
    check(
        "a key an earlier migration made in a literal loop is read from history",
        sorted(
            f.rule
            for f in analyze(
                {"m.py": rev("r2", "r1") + f"def upgrade():\n    with_lock_retries('runs')\n{drop_fk}\n"},
                _BASE_MODELS,
                looped_history,
            )
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a loop whose body rebinds its variable is not unrolled",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    for ref in ('users',):\n"
            "        ref = other()\n"
            "        op.create_foreign_key(None, 'task', ref, ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["unresolved"],
    )
    check(
        "a key added with a column on an unreadable table is not passed silently",
        _rules_found(
            "    with_lock_retries('users')\n"
            "    for t in tables():\n"
            "        op.add_column(t, sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), index=True))"
        ),
        ["unresolved"],
    )
    check(
        "a loop variable is not bound after its loop",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    for ref in ('users',):\n"
            "        pass\n"
            "    op.create_foreign_key(None, 'task', ref, ['u'], ['id'], postgresql_not_valid=True)"
        ),
        ["unresolved"],
    )
    check(
        "a schema-qualified table in a literal loop is the table it names",
        _rules_found(
            "    with_lock_retries('task, users')\n"
            "    for ref in ('public.users',):\n"
            "        op.create_foreign_key(None, 'task', ref, ['u'], ['id'], postgresql_not_valid=True)"
        ),
        [],
    )
    check(
        "a key dropped by name in a literal loop is read from history",
        judged(
            "    with_lock_retries('runs')\n"
            "    for t in ('runs',):\n"
            "        op.drop_constraint(f'{t}_topic_id_fkey', t, type_='foreignkey')",
            created,
        ),
        ["fk-table-not-locked"],
    )
    check(
        "a concurrent index in an autocommit block on a loop-variable table",
        _rules_found(
            "    with op.get_context().autocommit_block():\n"
            "        for t in ('task', 'tasks'):\n"
            "            op.create_index('ix', t, ['x'], postgresql_concurrently=True)"
        ),
        [],
    )
    check(
        "a NOT VALID foreign key on a loop-variable table",
        _rules_found(
            "    for t in ('task',):\n"
            "        op.create_foreign_key('fk', t, 'users', ['owner_id'], ['id'], postgresql_not_valid=True)"
        ),
        [],
    )
    check(
        "op.create_foreign_key NOT VALID, then VALIDATE in the same migration",
        _rules_found(
            "    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)\n"
            "    op.execute('ALTER TABLE task VALIDATE CONSTRAINT fk')"
        ),
        ["validate-outside-autocommit"],
    )
    check(
        "op.create_foreign_key NOT VALID, then VALIDATE in an autocommit block",
        _rules_found(
            "    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)\n"
            "    with op.get_context().autocommit_block():\n"
            "        op.execute('ALTER TABLE task VALIDATE CONSTRAINT fk')"
        ),
        [],
    )
    check(
        "a NOT VALID constraint with no VALIDATE in this migration",
        _rules_found(
            "    op.create_foreign_key('fk', 'task', 'users', ['owner_id'], ['id'], postgresql_not_valid=True)"
        ),
        [],
    )
    check(
        "a lone VALIDATE in its own migration",
        _rules_found("    op.execute('ALTER TABLE task VALIDATE CONSTRAINT fk')"),
        [],
    )
    check(
        "raw SQL: NOT VALID added, validated in an autocommit block",
        _rules_found(
            '    op.execute("ALTER TABLE task ADD CONSTRAINT fk FOREIGN KEY (owner_id) REFERENCES users (id) NOT VALID")\n'
            "    with op.get_context().autocommit_block():\n"
            '        op.execute("ALTER TABLE task VALIDATE CONSTRAINT fk")'
        ),
        [],
    )
    check(
        "a CHECK added NOT VALID, then VALIDATE in the same migration",
        _rules_found(
            "    op.create_check_constraint('ck', 'task', 'video_url IS NOT NULL', postgresql_not_valid=True)\n"
            "    op.execute('ALTER TABLE task VALIDATE CONSTRAINT ck')"
        ),
        ["validate-outside-autocommit"],
    )

    # Found in review (2026-10-07): an empty downgrade has to say why. The
    # historical migrations are the reason for the rule — a `pass` on its own
    # reads the same whether it was thought about or never written.
    check("a bare pass downgrade", _downgrade_rules("    pass"), ["empty-downgrade"])
    check(
        "a downgrade with an empty docstring",
        _downgrade_rules('    """"""\n    pass'),
        ["empty-downgrade"],
    )
    check(
        "the Alembic template's downgrade docstring is not a reason",
        _downgrade_rules('    """Downgrade schema."""\n    pass'),
        ["empty-downgrade"],
    )
    check(
        "a pass downgrade that says what it leaves in place",
        _downgrade_rules('    """Nothing to put back: the previous code reads it as empty."""\n    pass'),
        [],
    )
    check(
        "a downgrade that raises",
        _downgrade_rules('    raise NotImplementedError("b 无法反向，恢复整库转储")'),
        [],
    )
    check(
        "a real downgrade is not judged for what it does",
        _downgrade_rules("    op.drop_column('task', 'video_url')"),
        [],
    )

    # --- a file that does not parse is not a clean file ----------------------
    # `unresolved` is a warning for one statement whose SQL is dynamic. The same
    # name used to cover a whole file that does not parse, so a migration with a
    # syntax error was reported as a warning and the run exited 0.
    broken = {"m.py": _HEAD + "\ndef upgrade() -> None:\n    op.drop_column('task', 'video_url'\n"}
    unparseable = analyze(broken, _BASE_MODELS)
    check("a migration that does not parse", [f.rule for f in unparseable], ["unparseable"])
    check("... is an error, not a warning", [f.is_error for f in unparseable], [True])

    # --- a base it cannot read is not a clean tree ---------------------------
    # The local path through `main()` that could not reach `--base` wrote a note
    # and exited 0, which the pre-commit hook read exactly as "read the added
    # migrations and cleared them". Driven through the real command in a real
    # repository: the exit code, and the `git rev-parse` that runs before the
    # read, are the whole of what is asserted here, and the pure functions above
    # cannot see either.
    lab = Path(tempfile.mkdtemp(prefix="migration-safety-self-test-"))
    lab_env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    lab_env.pop("GIT_DIR", None)
    # `CI` goes too, so that the child is the local path these assertions are
    # about. The code they pin read it: under `CI` an unreachable base already
    # exited 2, so leaving it set would let the unfixed script pass whenever the
    # self-test ran in CI — which is the only place it runs on its own.
    lab_env.pop("CI", None)
    try:
        versions = lab / "backend" / "alembic" / "versions"
        versions.mkdir(parents=True)
        (versions / "x.py").write_text(_HEAD + "\ndef upgrade() -> None:\n    pass\n", encoding="utf-8")

        def git(*argv: str) -> None:
            done = subprocess.run(
                ["git", "-c", "commit.gpgsign=false", *argv],
                cwd=lab,
                capture_output=True,
                text=True,
                env=lab_env,
            )
            if done.returncode != 0:
                raise AssertionError(
                    f"self-test fixture: git {' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}"
                )

        git("init", "-q", "-b", "main")
        git("config", "user.email", "self-test@example.invalid")
        git("config", "user.name", "self-test")
        git("add", "-A")
        git("commit", "-qm", "x")

        # Both shapes of the command: with nothing named it has to work out the
        # added set itself, and with a file named it can read that one but not
        # the previous release's models. Neither can finish without the base.
        for label, argv in (
            ("with no files named", []),
            ("with a file named", ["backend/alembic/versions/x.py"]),
        ):
            unavailable = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--base", "no-such-base-ref", *argv],
                cwd=lab,
                capture_output=True,
                text=True,
                env=lab_env,
            )
            check(f"an unavailable --base is refused {label}", unavailable.returncode, EXIT_CANNOT_JUDGE)
            check(f"... and no verdict is printed {label}", "PASS" in unavailable.stdout, False)

        # Through the real command, with a base it can read: one untracked
        # migration whose syntax is broken must exit 1 and never print PASS.
        (versions / "broken.py").write_text(
            _HEAD + "\ndef upgrade() -> None:\n    op.drop_column('task', 'video_url'\n",
            encoding="utf-8",
        )
        broken_run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--base", "main"],
            cwd=lab,
            capture_output=True,
            text=True,
            env=lab_env,
        )
        check("a migration that does not parse is refused", broken_run.returncode, 1)
        check("... and it is named", "[unparseable]" in broken_run.stdout, True)
        check("... and no verdict is printed", "PASS" in broken_run.stdout, False)

        # A BOM is valid to Python's file reader; it must not read as unparseable.
        (versions / "broken.py").unlink()
        (versions / "bom.py").write_bytes(
            b"\xef\xbb\xbf" + (_HEAD + "\ndef upgrade() -> None:\n    pass\n").encode(),
        )
        bom_run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--base", "main"],
            cwd=lab,
            capture_output=True,
            text=True,
            env=lab_env,
        )
        check("a BOM-prefixed migration is read", bom_run.returncode, 0)
        check("... and is not called unparseable", "unparseable" in bom_run.stdout, False)

        # A key dropped by name: the table it points at is known only from a
        # migration already on the base, which the command reads as history.
        (versions / "made.py").write_text(
            _HEAD + "\ndef upgrade() -> None:\n"
            "    op.create_table('runs', sa.Column('topic_id', sa.Integer(), sa.ForeignKey('topics.id')))\n",
            encoding="utf-8",
        )
        git("add", "backend/alembic/versions/made.py")
        git("commit", "-qm", "made")
        (versions / "drops.py").write_text(
            _HEAD + "\ndef upgrade() -> None:\n    with_lock_retries('runs')\n"
            "    op.drop_constraint('runs_topic_id_fkey', 'runs', type_='foreignkey')\n",
            encoding="utf-8",
        )
        history_run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--base", "main"],
            cwd=lab,
            capture_output=True,
            text=True,
            env=lab_env,
        )
        check("a dropped key's target read from the base is judged", history_run.returncode, 1)
        check("... as not locked", "[fk-table-not-locked]" in history_run.stdout, True)
    finally:
        shutil.rmtree(lab, ignore_errors=True)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print(
        "PASS: check-migration-safety self-test (drop/rename, index, constraints, columns, imports, "
        "unreadable names, NOT VALID/VALIDATE, empty downgrade, unparseable files, exceptions, and "
        "the refusal of a --base it cannot read)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
