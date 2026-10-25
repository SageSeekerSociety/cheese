"""Merge another Cheese deployment's export into this deployment, one batch at a time.

    python -m scripts.merge_deployment import --batch B --source-url URL \\
        [--source-uploads DIR] [--manifest FILE] [--source-columns FILE] [--dry-run]
    python -m scripts.merge_deployment revert --batch B [--dry-run]

``deploy/merge-deployment.sh`` runs this on the box, beside the running backend
and with its settings: it restores the export into a scratch database, migrates
that to this release's head, and passes it here as ``--source-url``. So the two
databases have the same schema, and every secret in the source is already
sealed under this deployment's key (``app.core.crypto``), bound to the source's
own rows.

What comes over, for each kind in ``KINDS`` (parents before children):

- A source row whose primary key is here with the same identity (its
  ``created_at``, or the columns its kind names) is the same row: both
  deployments have it from the history they share. It changes only if the
  manifest lists it as changed on the source since then. Then the source wins
  for the columns the source deployment itself had (``--source-columns``: a
  column a migration added on the way here holds this deployment's value),
  except when this row's ``updated_at`` is later than the source's: both sides
  changed it, this side last, so it stays, and the report names it.
- A source row an earlier batch inserted is that row here, under the key it
  got then, and stays as that batch wrote it: the same export under another
  batch name brings nothing twice.
- Any other source row is new. If a row here already is that thing by its
  natural key (an account by email, an OAuth link by provider id, a passkey by
  credential, a profile by its user, a membership by who and what it joins),
  the source row becomes that row and nothing is written. Otherwise it is
  inserted, keeping its id when the id is free here and taking the next one
  from this deployment's sequence when it is not. Every column that names a row of a kind here — a foreign key or not,
  polymorphic or inside JSON — is rewritten to the id that row has here.
- An account whose email is under a domain the manifest lists is not brought,
  and neither is any row that names one.
- A new account whose username is taken here, by a person or as a team's
  handle, gets ``_<its id>`` appended; a new team whose handle is taken gets
  ``team-<its id>``.

Rows this deployment derives are not copied: a team that is someone's personal
team (this deployment makes one on first use), and a task's versions and its
题目说明 file, which are recorded for each new task the way publishing records
them (``app.domain.task.package.record``). Files under the source's uploads
tree that are not here are copied; a path that is here with other bytes stays as
it is and is reported. A table outside ``KINDS`` is never written; the report
counts the source rows it has that are not here.

Everything a batch writes is recorded in ``app.core.deployment_merges``. The
import is one transaction: it lands whole or not at all. A batch already in is
not imported again. ``revert`` takes one batch back: it deletes the rows the
batch inserted, puts back the columns it changed, removes the files it created
and moves each sequence it advanced back if nothing has used it since. It
refuses, changing nothing, while any of those rows differs from what the batch
wrote, or while a row made since names a row it inserted (through a foreign
key, or a reference column of a merged kind). A sequence goes back no lower
than the highest id still in its table. ``--dry-run`` does all of it inside a transaction that is rolled back,
copies no file, and prints the same report.

Every step is logged as a timestamped line on stdout; the wrapper keeps the log.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  (every table on the metadata, for the sequences)
from app.core import blob_store
from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from app.core.db import Base, async_session_factory
from app.domain.task import package
from app.domain.task.models import Task

Row = dict[str, Any]


def log(line: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    print(f"[{stamp}] {line}", flush=True)


@dataclass(frozen=True)
class Secret:
    """A column sealed for the row it is in (``app.core.crypto``)."""

    column: str
    purpose: Purpose
    bound_to: Callable[[Row, str], str]
    only_if: str | None = None  # a boolean column that says the row is sealed


@dataclass(frozen=True)
class Kind:
    table: str
    #: Columns that name a row of another kind: column -> that kind's table.
    refs: dict[str, str] = field(default_factory=dict)
    #: A column whose kind another column says: (column, says, {value: table}).
    poly: tuple[str, str, dict[Any, str]] | None = None
    #: Ids inside a JSON column: (column, key, key, ...) -> table. What the
    #: path ends at is an id or a list of ids.
    json_refs: dict[tuple[str, ...], str] = field(default_factory=dict)
    #: What makes a row with the same primary key the same row.
    identity: tuple[str, ...] = ("created_at",)
    #: Columns that already say which row here a new source row is (live rows).
    natural: tuple[str, ...] = ()
    secrets: tuple[Secret, ...] = ()


def _realname_binding(row: Row, column: str) -> str:
    return f"user:{row['user_id']}:{column}"


def _oauth_binding(row: Row, column: str) -> str:
    return f"user:{row['user_id']}:{row['provider_id']}:{column}"


USER = "user"

KINDS: list[Kind] = [
    Kind(USER),
    Kind("avatar"),
    Kind(
        "attachment",
        identity=("url",),
        json_refs={("meta", "uploaderId"): USER},
    ),
    Kind("material", refs={"uploader_id": USER}),
    Kind(
        "user_profile",
        refs={"user_id": USER, "avatar_id": "avatar"},
        natural=("user_id",),
    ),
    Kind(
        "user_o_auth_connection",
        refs={"user_id": USER},
        natural=("provider_id", "provider_user_id"),
        secrets=tuple(
            Secret(c, Purpose.OAUTH_TOKEN, _oauth_binding)
            for c in ("access_token", "refresh_token")
        ),
    ),
    Kind("passkey", refs={"user_id": USER}, natural=("credential_id",)),
    Kind(
        "user_real_name_identities",
        refs={"user_id": USER},
        natural=("user_id",),
        secrets=tuple(
            Secret(c, Purpose.REALNAME, _realname_binding, only_if="encrypted")
            for c in ("real_name", "student_id", "grade", "major", "class_name")
        ),
    ),
    Kind(
        "user_following_relationship",
        refs={"followee_id": USER, "follower_id": USER},
        natural=("followee_id", "follower_id"),
    ),
    Kind("tag", refs={"created_by_id": USER}),
    Kind(
        "space",
        refs={"avatar_id": "avatar", "default_category_id": "space_categories"},
        json_refs={("teaching", "material_ids"): "material"},
    ),
    Kind(
        "space_categories",
        refs={"space_id": "space"},
        json_refs={("teaching", "material_ids"): "material"},
    ),
    Kind(
        "space_admin_relation",
        refs={"space_id": "space", "user_id": USER},
        natural=("space_id", "user_id"),
    ),
    Kind("space_announcement", refs={"space_id": "space", "author_id": USER}),
    Kind(
        "space_classification_tag_relation", refs={"space_id": "space", "tag_id": "tag"}
    ),
    Kind("team", refs={"avatar_id": "avatar", "personal_owner_user_id": USER}),
    Kind(
        "team_user_relation",
        refs={"team_id": "team", "user_id": USER},
        natural=("team_id", "user_id"),
    ),
    Kind(
        "team_membership_application",
        refs={
            "user_id": USER,
            "team_id": "team",
            "initiator_id": USER,
            "processed_by_id": USER,
        },
    ),
    Kind(
        "task",
        refs={
            "creator_id": USER,
            "space_id": "space",
            "category_id": "space_categories",
            "team_id": "team",
            "reviewed_by": USER,
        },
        json_refs={("protocol_override", "teaching", "material_ids"): "material"},
    ),
    Kind(
        "user_real_name_access_logs",
        refs={"accessor_id": USER, "target_id": USER},
        poly=("module_entity_id", "module_type", {"SPACE": "space", "TASK": "task"}),
    ),
    Kind("task_submission_schema", refs={"task_id": "task"}, identity=()),
    Kind("task_rubric_item", refs={"task_id": "task"}, identity=()),
    Kind("task_stages", refs={"task_id": "task"}, identity=()),
    Kind("task_tag_relation", refs={"task_id": "task", "tag_id": "tag"}),
    Kind(
        "task_membership",
        refs={"task_id": "task"},
        poly=("member_id", "is_team", {True: "team", False: USER}),
        natural=("task_id", "member_id", "is_team"),
    ),
    Kind(
        "task_submission",
        refs={"membership_id": "task_membership", "submitter_id": USER},
    ),
    Kind(
        "task_submission_entry",
        refs={
            "task_submission_id": "task_submission",
            "content_attachment_id": "attachment",
        },
    ),
    Kind(
        "task_submission_review",
        refs={"submission_id": "task_submission", "reviewer_id": USER},
        natural=("submission_id", "reviewer_id"),
    ),
]

#: Tables this deployment fills itself for the rows above; never copied.
DERIVED = {"task_package_revisions", "library_files"}
#: Tables that are not anyone's data.
NOT_DATA = {
    "alembic_version",
    "spatial_ref_sys",
    "deployment_merge_batches",
    "deployment_merge_rows",
    "deployment_merge_files",
}

#: A file named by the id of the row it belongs to: (table, folder).
ID_NAMED_FILES = {("avatar", "avatars")}

UPLOADS = "uploads"


class Refused(Exception):
    """The merge cannot go ahead; the message says why. Nothing was written."""


# --- the shape of a table, read from the target -------------------------------


@dataclass
class Table:
    name: str
    columns: list[str]
    pk: list[str]
    identity_column: bool
    sequence: str | None


async def describe(session: AsyncSession, name: str) -> Table:
    rows = (
        await session.execute(
            text(
                "SELECT a.attname, a.attidentity <> '' AS ident,"
                " a.attnum = ANY(i.indkey) AS in_pk"
                " FROM pg_attribute a"
                " LEFT JOIN pg_index i ON i.indrelid = a.attrelid AND i.indisprimary"
                " WHERE a.attrelid = to_regclass(:t) AND a.attnum > 0"
                " AND NOT a.attisdropped AND a.attgenerated = ''"
                " ORDER BY a.attnum"
            ),
            {"t": f'public."{name}"'},
        )
    ).all()
    if not rows:
        raise Refused(f"table {name} does not exist here")
    columns = [r.attname for r in rows]
    pk = [r.attname for r in rows if r.in_pk]
    sequence = None
    if len(pk) == 1:
        sequence = await session.scalar(
            text("SELECT pg_get_serial_sequence(:t, :c)"),
            {"t": f'public."{name}"', "c": pk[0]},
        )
        if sequence is None:
            # Ids drawn by the application from a named sequence (the model's
            # ``Sequence``), not by a column default.
            model = Base.metadata.tables.get(name)
            default = model.c[pk[0]].default if model is not None else None
            if default is not None and hasattr(default, "name"):
                sequence = f"public.{default.name}"
    return Table(name, columns, pk, any(r.ident for r in rows), sequence)


def _q(name: str) -> str:
    return f'"{name}"'


# --- planning -----------------------------------------------------------------


@dataclass
class Plan:
    """What the batch does, worked out before anything is written."""

    #: (table, source key) -> key here, for every source row that has one.
    ids: dict[str, dict[Any, Any]] = field(default_factory=lambda: defaultdict(dict))
    #: (table, source key) of rows not brought, with why.
    skipped: dict[str, dict[Any, str]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    inserts: list[tuple[str, Row, Row]] = field(
        default_factory=list
    )  # table, row, source
    updates: list[tuple[str, Row, Row, Row]] = field(
        default_factory=list
    )  # table, key, changes, source
    report: dict[str, Any] = field(default_factory=lambda: defaultdict(Counter))
    notes: list[str] = field(default_factory=list)
    #: Rows earlier batches brought: table -> source key (JSON) -> key here.
    earlier: dict[str, dict[str, Row]] = field(default_factory=dict)
    #: Every live username and team handle here, lower-cased.
    handles: set[str] = field(default_factory=set)


def _key(table: Table, row: Row) -> Any:
    values = tuple(row[c] for c in table.pk)
    return values[0] if len(values) == 1 else values


def _key_dict(table: Table, row: Row) -> Row:
    return {c: row[c] for c in table.pk}


class Allocator:
    """New ids. A dry run hands out negative placeholders and touches no
    sequence; an import takes them from this deployment's sequences, after
    moving each past the ids the batch keeps, and records where each was."""

    def __init__(self, session: AsyncSession, dry: bool) -> None:
        self._session = session
        self._dry = dry
        self._next = 0
        self.moved: dict[str, dict[str, Any]] = {}

    async def _remember(self, sequence: str, table: Table) -> None:
        if sequence not in self.moved:
            before = (
                await self._session.execute(
                    text(f"SELECT last_value, is_called FROM {sequence}")
                )
            ).one()
            self.moved[sequence] = {
                "before": [before.last_value, before.is_called],
                # Whose ids it hands out: a revert never moves it below them.
                "table": table.name,
                "column": table.pk[0],
            }

    async def track(self, table: Table) -> None:
        """Record where ``table``'s sequence is before code of this
        deployment draws ids from it on the batch's behalf."""
        if table.sequence is not None and not self._dry:
            await self._remember(table.sequence, table)

    async def keep_above(self, table: Table, kept: Iterable[int]) -> None:
        top = max(kept, default=None)
        if top is None or table.sequence is None or self._dry:
            return
        current = await self._session.scalar(
            text(
                f"SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END FROM {table.sequence}"
            )
        )
        if top > current:
            await self._remember(table.sequence, table)
            await self._session.execute(
                text("SELECT setval(:s, :v)"), {"s": table.sequence, "v": top}
            )

    async def new(self, table: Table) -> int:
        if table.sequence is None:
            raise Refused(f"{table.name} has no id sequence to take a new id from")
        if self._dry:
            self._next -= 1
            return self._next
        await self._remember(table.sequence, table)
        return int(
            await self._session.scalar(
                text("SELECT nextval(:s)"), {"s": table.sequence}
            )
        )

    async def finish(self) -> dict[str, Any]:
        for sequence, entry in self.moved.items():
            after = (
                await self._session.execute(
                    text(f"SELECT last_value, is_called FROM {sequence}")
                )
            ).one()
            entry["after"] = [after.last_value, after.is_called]
        return self.moved


@dataclass
class Options:
    batch: str
    dry: bool
    changed: dict[str, set[Any]]
    skip_domains: tuple[str, ...]
    native: dict[str, set[str]] | None


def _ref_target(kind: Kind, row: Row, column: str) -> str | None:
    if column in kind.refs:
        return kind.refs[column]
    if kind.poly and kind.poly[0] == column:
        return kind.poly[2].get(row.get(kind.poly[1]))
    return None


ORDER = {k.table: i for i, k in enumerate(KINDS)}


def _translate(
    plan: Plan, kind: Kind, row: Row, *, forward: bool = False
) -> tuple[Row, str | None]:
    """The row with every reference rewritten to the id it has here; or the
    reason it cannot come (it names a row that is not brought).

    Planning goes kind by kind, so a reference to a kind planned later (a space
    naming its default category) is rewritten afterwards, on its own:
    ``forward`` rewrites only those, and only once."""
    out = dict(row)
    columns = [
        c for c in kind.refs if (ORDER[kind.refs[c]] >= ORDER[kind.table]) == forward
    ]
    if kind.poly and not forward:
        column, says, tables = kind.poly
        if out.get(column) is not None and out.get(says) not in tables:
            return out, f"{column} names a {out.get(says)!r}, which is not merged"
        columns.append(column)
    for column in columns:
        target = _ref_target(kind, out, column)
        value = out.get(column)
        if target is None or value is None:
            continue
        if value in plan.skipped[target]:
            return out, f"{column} names {target} {value}, which is not brought"
        if value in plan.ids[target]:
            out[column] = plan.ids[target][value]
    for (column, *path), target in ({} if forward else kind.json_refs).items():
        document, refused = _translate_json(plan, out.get(column), path, target)
        if refused:
            return (
                out,
                f"{column}.{'.'.join(path)} names {target} {refused}, which is not brought",
            )
        out[column] = document
    return out, None


def _translate_json(
    plan: Plan, document: Any, path: list[str], target: str
) -> tuple[Any, Any]:
    """``document`` with the id or ids at ``path`` rewritten; or the id that is
    not brought. A path that is not there leaves the document as it is."""
    if not path:
        values = document if isinstance(document, list) else [document]
        for value in values:
            if value in plan.skipped[target]:
                return document, value
        mapped = [plan.ids[target].get(v, v) for v in values]
        return (mapped if isinstance(document, list) else mapped[0]), None
    if not isinstance(document, dict) or document.get(path[0]) is None:
        return document, None
    inner, refused = _translate_json(plan, document[path[0]], path[1:], target)
    return {**document, path[0]: inner}, refused


def _reseal(kind: Kind, source: Row, row: Row) -> Row:
    """Secrets opened as bound to the source row and sealed for the row here."""
    for secret in kind.secrets:
        value = source.get(secret.column)
        if not value or (secret.only_if and not source.get(secret.only_if)):
            continue
        plain = decrypt(
            secret.purpose, value, bound_to=secret.bound_to(source, secret.column)
        )
        row[secret.column] = encrypt(
            secret.purpose, plain, bound_to=secret.bound_to(row, secret.column)
        )
    return row


def _open(kind: Kind, row: Row) -> Row:
    """The row with its secrets as plain text, to compare two sides."""
    out = dict(row)
    for secret in kind.secrets:
        value = row.get(secret.column)
        if value and not (secret.only_if and not row.get(secret.only_if)):
            out[secret.column] = decrypt(
                secret.purpose, value, bound_to=secret.bound_to(row, secret.column)
            )
    return out


async def _target_rows(session: AsyncSession, table: Table) -> list[Row]:
    rows = await session.execute(text(f"SELECT to_jsonb(x) FROM {_q(table.name)} x"))
    return [r[0] for r in rows]


async def _source_rows(source: asyncpg.Connection, table: Table) -> list[Row]:
    order = ", ".join(_q(c) for c in table.pk)
    rows = await source.fetch(
        f"SELECT to_jsonb(x)::text FROM {_q(table.name)} x ORDER BY {order}"
    )
    return [json.loads(r[0]) for r in rows]


def _natural(kind: Kind, row: Row) -> tuple | None:
    if not kind.natural:
        return None
    values = tuple(row.get(c) for c in kind.natural)
    if any(v is None for v in values):
        return None
    return values


def _later(a: str | None, b: str | None) -> bool:
    """Whether timestamp ``a`` is after ``b`` (both as ``to_jsonb`` writes them)."""
    if a is None or b is None:
        return False
    return datetime.fromisoformat(a) > datetime.fromisoformat(b)


def _lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


async def plan_kind(
    session: AsyncSession,
    source: asyncpg.Connection,
    plan: Plan,
    kind: Kind,
    table: Table,
    alloc: Allocator,
    options: Options,
) -> None:
    counts = plan.report["kinds"].setdefault(kind.table, Counter())
    here = await _target_rows(session, table)
    by_key = {_key(table, r): r for r in here}
    live = [r for r in here if r.get("deleted_at") is None]
    natural = {_natural(kind, r): r for r in live if _natural(kind, r) is not None}
    emails = {_lower(r["email"]): r for r in live} if kind.table == USER else {}
    changed = options.changed.get(kind.table, set())
    earlier = plan.earlier.get(kind.table, {})
    native = options.native.get(kind.table) if options.native is not None else None

    pending: list[tuple[Row, Row]] = []  # (translated row, source row) needing an id
    for source_row in await _source_rows(source, table):
        source_key = _key(table, source_row)
        row, refused = _translate(plan, kind, source_row)
        if (
            kind.table == "team"
            and source_row.get("personal_owner_user_id") is not None
        ):
            refused = refused or "a personal team: this deployment makes its own"
        # A row an earlier batch brought is that row here, under the key it got.
        prior = earlier.get(json.dumps(_key_dict(table, source_row), sort_keys=True))
        key = _key(table, prior) if prior is not None else _key(table, row)
        same = by_key.get(key)
        if same is not None and (
            prior is not None or all(same.get(c) == row.get(c) for c in kind.identity)
        ):
            plan.ids[kind.table][source_key] = key
            if prior is not None:
                # An earlier batch inserted it as it saw fit (a new id, a new
                # name); what it holds here is that batch's to answer for.
                counts["brought by an earlier batch"] += 1
                continue
            if refused:
                # Already here: it stays as it is, and what names it still can.
                counts["here already; not changed (names a row not brought)"] += 1
                continue
            for c in table.pk:
                row[c] = same[c]
            await _plan_shared(
                plan, kind, table, row, same, source_row, changed, native, counts
            )
            continue
        if refused:
            plan.skipped[kind.table][source_key] = refused
            counts["skipped"] += 1
            log(f"skip {kind.table} {source_key}: {refused}")
            continue
        # A new row.
        if kind.table == USER:
            email = _lower(row.get("email"))
            domain = (email or "").rpartition("@")[2]
            if domain and domain in options.skip_domains:
                plan.skipped[USER][source_key] = f"an account under {domain}"
                counts["skipped"] += 1
                log(f"skip user {source_key}: account under {domain}")
                continue
            if row.get("deleted_at") is None and email in emails:
                into = emails[email]["id"]
                plan.ids[USER][source_key] = into
                counts["merged"] += 1
                plan.report["merged_accounts"].append([source_key, into])
                log(f"merge user {source_key} into {into} (same email)")
                continue
        match = natural.get(_natural(kind, row))
        if match is not None and row.get("deleted_at") is None:
            plan.ids[kind.table][source_key] = _key(table, match)
            counts["already here"] += 1
            log(
                f"same {kind.table} {source_key} is {_key(table, match)} here by {kind.natural}"
            )
            continue
        pending.append((row, source_row))
        if _natural(kind, row) is not None and row.get("deleted_at") is None:
            natural[_natural(kind, row)] = row

    # Ids: keep the source's where it is free, and move the sequence past the
    # kept ones before drawing any new one, so the two never meet.
    keep = []
    if len(table.pk) == 1:
        for row, _ in pending:
            if _key(table, row) not in by_key:
                keep.append(_key(table, row))
        await alloc.keep_above(table, [k for k in keep if isinstance(k, int)])
    taken = set(by_key)
    for row, source_row in pending:
        source_key = _key(table, source_row)
        key = _key(table, row)
        if key in taken:
            if len(table.pk) != 1:
                raise Refused(
                    f"{kind.table} {key}: the key is taken here by another row"
                )
            row[table.pk[0]] = await alloc.new(table)
            key = row[table.pk[0]]
            counts["inserted with a new id"] += 1
        else:
            counts["inserted"] += 1
        taken.add(key)
        plan.ids[kind.table][source_key] = key
        if kind.table == USER and row.get("deleted_at") is None:
            # One handle names one person or one team (``handle_is_taken``).
            name = _lower(row["username"])
            if name in plan.handles:
                renamed = f"{row['username']}_{key}"
                plan.report["renamed_accounts"].append(
                    [source_key, row["username"], renamed]
                )
                log(
                    f"user {source_key}: username {row['username']!r} is taken here, now {renamed!r}"
                )
                row["username"] = renamed
            plan.handles.add(_lower(row["username"]))
        if kind.table == "team" and row.get("handle"):
            if _lower(row["handle"]) in plan.handles:
                renamed = f"team-{key}"
                plan.report["renamed_teams"].append(
                    [source_key, row["handle"], renamed]
                )
                log(
                    f"team {source_key}: handle {row['handle']!r} is taken here, now {renamed!r}"
                )
                row["handle"] = renamed
            plan.handles.add(_lower(row["handle"]))
            if row.get("join_token") and row["join_token"] in {
                r.get("join_token") for r in here
            }:
                row["join_token"] = None
        plan.inserts.append((kind.table, row, source_row))
        log(f"insert {kind.table} {source_key} as {key}")


async def _plan_shared(
    plan: Plan,
    kind: Kind,
    table: Table,
    row: Row,
    here: Row,
    source_row: Row,
    changed: set[Any],
    native: set[str] | None,
    counts: Counter,
) -> None:
    source_key = _key(table, source_row)
    secret = {s.column for s in kind.secrets}
    columns = [
        c
        for c in table.columns
        if c not in table.pk and c in row and (native is None or c in native)
    ]
    # Sealed values differ byte for byte whatever they hold (each seal draws a
    # new nonce), so they are opened, and only for a row the source changed.
    differ = [c for c in columns if c not in secret and row.get(c) != here.get(c)]
    if source_key in changed and secret:
        mine, theirs = _open(kind, source_row), _open(kind, here)
        differ += [c for c in columns if c in secret and mine.get(c) != theirs.get(c)]
    if not differ:
        counts["same"] += 1
        return
    if source_key not in changed:
        counts["differs, kept here"] += 1
        return
    if _later(here.get("updated_at"), source_row.get("updated_at")):
        counts["changed on both sides, kept here"] += 1
        plan.report["conflicts"].append(
            {
                "table": kind.table,
                "key": source_key,
                "columns": differ,
                "here_updated_at": here["updated_at"],
                "source_updated_at": source_row["updated_at"],
            }
        )
        log(
            f"conflict {kind.table} {source_key}: changed on both sides, this side later; kept ({differ})"
        )
        return
    changes = {c: row[c] for c in differ}
    if kind.secrets:
        resealed = _reseal(kind, source_row, dict(row))
        changes = {c: resealed[c] for c in differ}
    plan.updates.append((kind.table, _key_dict(table, row), changes, source_row))
    counts["updated"] += 1
    log(f"update {kind.table} {source_key}: {differ}")


# --- writing ------------------------------------------------------------------


class Journal:
    def __init__(self, session: AsyncSession, batch: str) -> None:
        self._session = session
        self._batch = batch
        self._position = 0
        self.files: list[tuple[str, str]] = []

    async def row(
        self,
        table: str,
        target_key: Row,
        source_key: Row | None,
        previous: Row | None,
        written: Row,
    ) -> None:
        self._position += 1
        await self._session.execute(
            text(
                "INSERT INTO deployment_merge_rows"
                " (batch, position, table_name, target_key, source_key, previous, written)"
                " VALUES (:b, :p, :t, CAST(:k AS jsonb), CAST(:s AS jsonb),"
                " CAST(:prev AS jsonb), CAST(:w AS jsonb))"
            ),
            {
                "b": self._batch,
                "p": self._position,
                "t": table,
                "k": json.dumps(target_key),
                "s": None if source_key is None else json.dumps(source_key),
                "prev": None if previous is None else json.dumps(previous),
                "w": json.dumps(written),
            },
        )

    async def file(self, store: str, key: str) -> None:
        self.files.append((store, key))
        await self._session.execute(
            text(
                "INSERT INTO deployment_merge_files (batch, store, key) VALUES (:b, :s, :k)"
            ),
            {"b": self._batch, "s": store, "k": key},
        )


def _where(table: Table, key: Row) -> tuple[str, dict[str, Any]]:
    """``WHERE`` on the primary key, compared as the key's own column types."""
    parts = []
    params = {}
    for i, column in enumerate(table.pk):
        parts.append(
            f"{_q(column)} = (jsonb_populate_record(NULL::{_q(table.name)}, CAST(:k{i} AS jsonb))).{_q(column)}"
        )
        params[f"k{i}"] = json.dumps({column: key[column]})
    return " AND ".join(parts), params


async def _read(session: AsyncSession, table: Table, key: Row) -> Row | None:
    where, params = _where(table, key)
    return await session.scalar(
        text(f"SELECT to_jsonb(x) FROM {_q(table.name)} x WHERE {where}"), params
    )


async def write_insert(session: AsyncSession, table: Table, row: Row) -> Row:
    columns = [c for c in table.columns if c in row]
    names = ", ".join(_q(c) for c in columns)
    overriding = " OVERRIDING SYSTEM VALUE" if table.identity_column else ""
    await session.execute(
        text(
            f"INSERT INTO {_q(table.name)} ({names}){overriding}"
            f" SELECT {names} FROM jsonb_populate_record(NULL::{_q(table.name)}, CAST(:r AS jsonb))"
        ),
        {"r": json.dumps(row)},
    )
    written = await _read(session, table, _key_dict(table, row))
    assert written is not None
    return written


async def write_update(
    session: AsyncSession, table: Table, key: Row, changes: Row
) -> tuple[Row, Row]:
    before = await _read(session, table, key)
    assert before is not None
    names = ", ".join(_q(c) for c in changes)
    where, params = _where(table, key)
    await session.execute(
        text(
            f"UPDATE {_q(table.name)} SET ({names}) = (SELECT {names} FROM"
            f" jsonb_populate_record(NULL::{_q(table.name)}, CAST(:r AS jsonb))) WHERE {where}"
        ),
        {"r": json.dumps(changes), **params},
    )
    after = await _read(session, table, key)
    assert after is not None
    return {c: before.get(c) for c in changes}, after


async def record_task_versions(
    session: AsyncSession, journal: Journal, tables: dict[str, Table], task_id: int
) -> None:
    """Version 1 of a new task and its 题目说明, as this deployment records them
    on publishing (``package.record``), dated when the task was published, as
    the versions of the tasks already here are."""
    task = await session.get(Task, task_id)
    assert task is not None
    revision = await package.record(
        session, task, by=task.creator_id, changes={package.PUBLISHED: True}
    )
    assert revision is not None
    await session.execute(
        text(
            "UPDATE task_package_revisions SET created_at = COALESCE(t.published_at, t.created_at)"
            " FROM task t WHERE t.id = :task AND task_package_revisions.id = :rev"
        ),
        {"task": task_id, "rev": revision.id},
    )
    rev_table = tables["task_package_revisions"]
    written = await _read(session, rev_table, {"id": revision.id})
    assert written is not None
    await journal.row(
        "task_package_revisions", {"id": revision.id}, None, None, written
    )
    files = await session.execute(
        text(
            "SELECT to_jsonb(x) FROM library_files x WHERE task_id = :t ORDER BY created_at"
        ),
        {"t": task_id},
    )
    for (row,) in files:
        await journal.row("library_files", {"id": row["id"]}, None, None, row)
        await journal.file(row["location"], row["blob_key"])


# --- uploads ------------------------------------------------------------------


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_files(
    plan: Plan, source_root: Path, target_root: Path
) -> list[tuple[Path, str]]:
    """(source file, path here) for each file to copy; conflicts reported."""
    renames: dict[str, str] = {}
    for table, folder in ID_NAMED_FILES:
        for old, new in plan.ids[table].items():
            if old != new:
                renames[f"{folder}/{old}"] = f"{folder}/{new}"
        for old in plan.skipped[table]:
            renames[f"{folder}/{old}"] = ""
    counts = plan.report["files"]
    copies: list[tuple[Path, str]] = []
    for path in sorted(p for p in source_root.rglob("*") if p.is_file()):
        relative = path.relative_to(source_root).as_posix()
        here = renames.get(relative, relative)
        if not here:
            counts["not brought (its row is not)"] += 1
            continue
        target = target_root / here
        if target.exists():
            if target.stat().st_size == path.stat().st_size and _digest(
                target
            ) == _digest(path):
                counts["same"] += 1
            else:
                counts["differs, kept here"] += 1
                plan.report["file_conflicts"].append(here)
                log(f"file {here}: here with other bytes; kept")
            continue
        counts["copied"] += 1
        copies.append((path, here))
    return copies


async def copy_files(
    journal: Journal, copies: list[tuple[Path, str]], target_root: Path
) -> None:
    for path, here in copies:
        target = target_root / here
        target.parent.mkdir(parents=True, exist_ok=True)
        # Never over a file that appeared since the plan: exclusive create.
        with path.open("rb") as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        await journal.file(UPLOADS, here)
        log(f"file {here}: copied")


async def remove_files(
    files: Iterable[tuple[str, str]], uploads_root: Path
) -> list[str]:
    failed = []
    for store, key in files:
        try:
            if store == UPLOADS:
                (uploads_root / key).unlink(missing_ok=True)
            else:
                await blob_store.store(store).delete(key)
            log(f"file {store}:{key}: removed")
        except Exception as exc:  # every file is tried; the ones left are listed
            failed.append(f"{store}:{key} ({exc})")
    return failed


# --- import -------------------------------------------------------------------


async def leftovers(
    session: AsyncSession, source: asyncpg.Connection
) -> dict[str, int]:
    """Source rows in tables this merge does not write, whose key is not here."""
    names = {k.table for k in KINDS} | DERIVED | NOT_DATA
    tables = await source.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )
    out = {}
    for (name,) in tables:
        if name in names:
            continue
        table = await describe(session, name)
        if not table.pk:
            count = await source.fetchval(f"SELECT count(*) FROM {_q(name)}")
            if count:
                out[name] = count
            continue
        keys = ", ".join(_q(c) for c in table.pk)
        query = f"SELECT to_jsonb(x)::text FROM (SELECT {keys} FROM {_q(name)}) x"
        rows = [json.loads(r[0]) for r in await source.fetch(query)]
        if not rows:
            continue
        here = {
            _key(table, r[0])
            for r in await session.execute(text(query.replace("::text", "")))
        }
        missing = sum(1 for r in rows if _key(table, r) not in here)
        if missing:
            out[name] = missing
    return out


async def run_import(
    *,
    batch: str,
    source_url: str,
    source_uploads: Path | None,
    manifest: dict[str, Any],
    native: dict[str, set[str]] | None,
    dry: bool,
) -> dict[str, Any]:
    changed = {t: set(keys) for t, keys in manifest.get("changed", {}).items()}
    options = Options(
        batch=batch,
        dry=dry,
        changed=changed,
        skip_domains=tuple(
            d.lower() for d in manifest.get("skip_account_email_domains", [])
        ),
        native=native,
    )
    uploads_root = Path(settings.storage_local_path)
    source = await asyncpg.connect(source_url)
    await source.execute("SET TIME ZONE 'UTC'")
    await source.execute("SET default_transaction_read_only = on")
    written_files: list[tuple[str, str]] = []
    try:
        async with async_session_factory() as session:
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            state = await session.scalar(
                text(
                    "SELECT state FROM deployment_merge_batches WHERE batch = :b FOR UPDATE"
                ),
                {"b": batch},
            )
            if state == "imported":
                log(f"batch {batch} is already in; nothing to do")
                return {"batch": batch, "already": True}
            here_rev = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            source_rev = await source.fetchval(
                "SELECT version_num FROM alembic_version"
            )
            if here_rev != source_rev:
                raise Refused(
                    f"the source is at {source_rev} and this database at {here_rev};"
                    " migrate the source to this release first"
                )
            plan = Plan()
            plan.report["merged_accounts"] = []
            plan.report["renamed_accounts"] = []
            plan.report["renamed_teams"] = []
            plan.report["conflicts"] = []
            plan.report["file_conflicts"] = []
            for table_name, source_key, target_key in await session.execute(
                text(
                    "SELECT r.table_name, r.source_key, r.target_key"
                    " FROM deployment_merge_rows r JOIN deployment_merge_batches b"
                    " USING (batch) WHERE b.state = 'imported' AND r.previous IS NULL"
                    " AND r.source_key IS NOT NULL"
                )
            ):
                plan.earlier.setdefault(table_name, {})[
                    json.dumps(source_key, sort_keys=True)
                ] = target_key
            plan.handles = {
                h
                for (h,) in await session.execute(
                    text(
                        # Closed accounts keep their names (``is_username_taken``)
                        # and so do deleted teams (``uq_team_handle_lower``
                        # covers every row).
                        'SELECT lower(username) FROM "user"'
                        " UNION SELECT lower(handle) FROM team WHERE handle IS NOT NULL"
                    )
                )
            }
            alloc = Allocator(session, dry)
            tables = {k.table: await describe(session, k.table) for k in KINDS}
            for derived in DERIVED:
                tables[derived] = await describe(session, derived)
            for kind in KINDS:
                await plan_kind(
                    session, source, plan, kind, tables[kind.table], alloc, options
                )
            unknown = set(changed) - set(tables)
            if unknown:
                plan.notes.append(
                    f"manifest names tables this merge does not write: {sorted(unknown)}"
                )

            await session.execute(
                text(
                    "INSERT INTO deployment_merge_batches"
                    " (batch, state, imported_at, sequences, report)"
                    " VALUES (:b, 'imported', now(), '{}', '{}')"
                    " ON CONFLICT (batch) DO UPDATE SET state = 'imported',"
                    " imported_at = now(), reverted_at = NULL"
                ),
                {"b": batch},
            )
            journal = Journal(session, batch)
            # Every file and blob the batch writes from here on, so that an
            # import that does not commit takes them away again.
            written_files = journal.files
            # Forward references (a space naming a category that comes later)
            # are known now that every kind has its ids.
            kinds = {k.table: k for k in KINDS}
            new_tasks = []
            for table_name, row, source_row in plan.inserts:
                kind = kinds[table_name]
                row, refused = _translate(plan, kind, row, forward=True)
                if refused:
                    raise Refused(
                        f"{table_name} {_key_dict(tables[table_name], source_row)}: {refused}"
                    )
                row = _reseal(kind, source_row, row)
                table = tables[table_name]
                written = await write_insert(session, table, row)
                await journal.row(
                    table_name,
                    _key_dict(table, row),
                    _key_dict(table, source_row),
                    None,
                    written,
                )
                if table_name == "task" and row.get("deleted_at") is None:
                    new_tasks.append(row["id"])
            for table_name, key, changes, source_row in plan.updates:
                table = tables[table_name]
                forward, refused = _translate(
                    plan,
                    kinds[table_name],
                    {
                        **changes,
                        **{
                            c: source_row[c]
                            for c, t in kinds[table_name].refs.items()
                            if c in changes and ORDER[t] >= ORDER[table_name]
                        },
                    },
                    forward=True,
                )
                if refused:
                    raise Refused(f"{table_name} {key}: {refused}")
                changes = {c: forward[c] for c in changes}
                previous, written = await write_update(session, table, key, changes)
                await journal.row(
                    table_name, key, _key_dict(table, source_row), previous, written
                )
            plan.report["derived"] = {"task versions recorded": len(new_tasks)}
            if not dry:
                await alloc.track(tables["task_package_revisions"])
                for task_id in new_tasks:
                    await record_task_versions(session, journal, tables, task_id)
                    log(f"task {task_id}: version 1 recorded")
            copies = (
                plan_files(plan, source_uploads, uploads_root)
                if source_uploads is not None
                else []
            )
            plan.report["leftovers"] = await leftovers(session, source)
            # A new task's files on the source copy, other than the 题目说明 this
            # deployment writes itself, would need their bytes carried from the
            # copy's own blob store: they are counted, not brought.
            task_files = await source.fetchval(
                "SELECT count(*) FROM library_files WHERE task_id = ANY($1::bigint[])"
                " AND name <> $2",
                [s["id"] for t, _, s in plan.inserts if t == "task"],
                package.BRIEF,
            )
            if task_files:
                plan.report["leftovers"]["library_files (files of new tasks)"] = (
                    task_files
                )
            report = _final(plan)
            if dry:
                await session.rollback()
                log("dry run: rolled back; no file copied")
                return report
            report["sequences"] = await alloc.finish()
            await copy_files(journal, copies, uploads_root)
            await session.execute(
                text(
                    "UPDATE deployment_merge_batches SET sequences = CAST(:s AS jsonb),"
                    " report = CAST(:r AS jsonb) WHERE batch = :b"
                ),
                {
                    "s": json.dumps(report["sequences"]),
                    "r": json.dumps(report, default=str),
                    "b": batch,
                },
            )
            await session.commit()
            written_files = []
            log(f"batch {batch} imported")
            return report
    finally:
        await source.close()
        if written_files:
            # The transaction did not commit: the files it wrote belong to nothing.
            await remove_files(written_files, uploads_root)


def _final(plan: Plan) -> dict[str, Any]:
    report = {
        k: (dict(v) if isinstance(v, Counter) else v) for k, v in plan.report.items()
    }
    report["kinds"] = {k: dict(v) for k, v in plan.report["kinds"].items()}
    report["notes"] = plan.notes
    return report


# --- revert -------------------------------------------------------------------


async def _referrers(
    session: AsyncSession,
    tables: dict[str, Table],
    inserted: dict[str, set[str]],
    journaled: dict[str, set[str]],
) -> list[str]:
    """Rows that name a row the batch inserted and are not the batch's own:
    through a foreign key, or through a reference column of a merged kind."""
    # (child table, column, parent table, condition on the child row)
    links: set[tuple[str, str, str, str]] = set()
    for r in await session.execute(
        text(
            "SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text"
            " FROM pg_constraint c JOIN pg_attribute a"
            " ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]"
            " WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1"
            " AND c.connamespace = 'public'::regnamespace"
        )
    ):
        links.add((r[0].strip('"'), r[1], r[2].strip('"'), "true"))
    for kind in KINDS:
        links |= {(kind.table, c, t, "true") for c, t in kind.refs.items()}
        if kind.poly:
            column, says, targets = kind.poly
            for value, t in targets.items():
                literal = (
                    str(value).lower() if isinstance(value, bool) else f"'{value}'"
                )
                links.add((kind.table, column, t, f"{_q(says)} = {literal}"))
    found = []
    for child, column, parent, condition in sorted(links):
        keys = inserted.get(parent)
        if not keys or parent not in tables or len(tables[parent].pk) != 1:
            continue
        pk = tables[parent].pk[0]
        child_table = tables.get(child) or await describe(session, child)
        tables[child] = child_table
        if not child_table.pk:
            continue
        kind_of = await session.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
                " WHERE attrelid = to_regclass(:t) AND attname = :c"
            ),
            {"t": f"public.{_q(child)}", "c": column},
        )
        columns = ", ".join(_q(c) for c in child_table.pk)
        rows = await session.execute(
            text(
                f"SELECT to_jsonb(x) FROM (SELECT {columns} FROM {_q(child)}"
                f" WHERE {_q(column)} = ANY(CAST(CAST(:ids AS text) AS {kind_of}[])) AND {condition}) x"
            ),
            {"ids": "{" + ",".join(str(json.loads(k)[pk]) for k in keys) + "}"},
        )
        mine = journaled.get(child, set())
        for (key,) in rows:
            if json.dumps(key, sort_keys=True) not in mine:
                found.append(f"{child} {key} ({column} -> {parent})")
    return found


async def run_revert(*, batch: str, dry: bool) -> dict[str, Any]:
    uploads_root = Path(settings.storage_local_path)
    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        found = (
            await session.execute(
                text(
                    "SELECT state, sequences FROM deployment_merge_batches WHERE batch = :b FOR UPDATE"
                ),
                {"b": batch},
            )
        ).one_or_none()
        if found is None or found.state != "imported":
            log(f"batch {batch} is not in; nothing to take back")
            return {"batch": batch, "nothing": True}
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, target_key, previous, written FROM deployment_merge_rows"
                    " WHERE batch = :b ORDER BY position DESC"
                ),
                {"b": batch},
            )
        ).all()
        files = [
            (r.store, r.key)
            for r in await session.execute(
                text(
                    "SELECT store, key FROM deployment_merge_files WHERE batch = :b ORDER BY id"
                ),
                {"b": batch},
            )
        ]
        tables: dict[str, Table] = {}
        drifted = []
        for r in rows:
            table = tables.get(r.table_name) or await describe(session, r.table_name)
            tables[r.table_name] = table
            now = await _read(session, table, r.target_key)
            if now != r.written:
                drifted.append(f"{r.table_name} {r.target_key}")
        inserted: dict[str, set[str]] = defaultdict(set)
        journaled: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            key = json.dumps(r.target_key, sort_keys=True)
            journaled[r.table_name].add(key)
            if r.previous is None:
                inserted[r.table_name].add(key)
        # A row the batch itself updated to point at one it inserted is put back
        # before that one goes (the revert walks the journal backwards).
        named = await _referrers(session, tables, inserted, journaled)
        if named:
            raise Refused(
                "rows made here since the import name rows it brought, so taking the"
                f" batch back would leave them pointing at nothing: {named[:20]}"
                f"{' …' if len(named) > 20 else ''}"
            )
        if drifted:
            raise Refused(
                "changed here since the import, so taking the batch back would lose"
                f" those changes: {drifted[:20]}{' …' if len(drifted) > 20 else ''}"
            )
        counts = Counter()
        for r in rows:
            table = tables[r.table_name]
            where, params = _where(table, r.target_key)
            if r.previous is None:
                await session.execute(
                    text(f"DELETE FROM {_q(table.name)} WHERE {where}"), params
                )
                counts[f"{r.table_name} deleted"] += 1
                log(f"delete {r.table_name} {r.target_key}")
            else:
                await write_update(session, table, r.target_key, r.previous)
                counts[f"{r.table_name} restored"] += 1
                log(f"restore {r.table_name} {r.target_key}: {sorted(r.previous)}")
        sequences = {}
        for sequence, entry in (found.sequences or {}).items():
            now = (
                await session.execute(
                    text(f"SELECT last_value, is_called FROM {sequence}")
                )
            ).one()
            # Ids this deployment drew while the import ran are in the table
            # now; the sequence goes back no further than the highest id there.
            top = await session.scalar(
                text(f"SELECT max({_q(entry['column'])}) FROM {_q(entry['table'])}")
            )
            if [now.last_value, now.is_called] != entry["after"]:
                sequences[sequence] = "used since the import; left where it is"
            elif top is not None and top > entry["before"][0]:
                if not dry:
                    await session.execute(
                        text("SELECT setval(:s, :v, true)"), {"s": sequence, "v": top}
                    )
                sequences[sequence] = f"moved back to {top}, the highest id still there"
            else:
                if not dry:
                    await session.execute(
                        text("SELECT setval(:s, :v, :c)"),
                        {
                            "s": sequence,
                            "v": entry["before"][0],
                            "c": entry["before"][1],
                        },
                    )
                sequences[sequence] = "moved back"
            log(f"sequence {sequence}: {sequences[sequence]}")
        await session.execute(
            text("DELETE FROM deployment_merge_rows WHERE batch = :b"), {"b": batch}
        )
        await session.execute(
            text("DELETE FROM deployment_merge_files WHERE batch = :b"), {"b": batch}
        )
        await session.execute(
            text(
                "UPDATE deployment_merge_batches SET state = 'reverted', reverted_at = now()"
                " WHERE batch = :b"
            ),
            {"b": batch},
        )
        report = {
            "batch": batch,
            "rows": dict(counts),
            "files": len(files),
            "sequences": sequences,
        }
        if dry:
            await session.rollback()
            log("dry run: rolled back; no file removed")
            return report
        await session.commit()
    report["files_not_removed"] = await remove_files(files, uploads_root)
    log(f"batch {batch} reverted")
    return report


# --- command line -------------------------------------------------------------


def _load_json(path: Path | None) -> Any:
    return json.loads(path.read_text()) if path else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--batch", required=True)
    # Or MERGE_SOURCE_URL, which keeps the scratch password off the command line.
    imp.add_argument("--source-url", default=os.environ.get("MERGE_SOURCE_URL"))
    imp.add_argument("--source-uploads", type=Path)
    imp.add_argument("--manifest", type=Path)
    imp.add_argument("--source-columns", type=Path)
    imp.add_argument("--dry-run", action="store_true")
    rev = sub.add_parser("revert")
    rev.add_argument("--batch", required=True)
    rev.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "import" and not args.source_url:
        parser.error("--source-url (or MERGE_SOURCE_URL) is required")
    try:
        if args.action == "import":
            native = _load_json(args.source_columns)
            report = asyncio.run(
                run_import(
                    batch=args.batch,
                    source_url=args.source_url,
                    source_uploads=args.source_uploads,
                    manifest=_load_json(args.manifest) or {},
                    native={t: set(c) for t, c in native.items()} if native else None,
                    dry=args.dry_run,
                )
            )
        else:
            report = asyncio.run(run_revert(batch=args.batch, dry=args.dry_run))
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
