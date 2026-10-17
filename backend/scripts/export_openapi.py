"""The API's OpenAPI document, printed to whoever asks for it, its digest, and the
untyped-route ratchet.

    uv run python -m scripts.export_openapi --stdout   print the document
    uv run python -m scripts.export_openapi            refresh digest and ratchet
    uv run python -m scripts.export_openapi --check    exit 1 if either is stale

The document is ``app.openapi()`` as the running app serves it, and it is NOT
committed — it used to be ``backend/openapi.json``. A route change rewrote 1.8 MB
of it, so every PR that touched a route carried that rewrite (44 in the sixty days
to 2026-10-14) and any two of them collided in a file neither had a decision in.
Its one reader is the frontend, which needs the types it compiles against
(``frontend/scripts/gen-api-types.mjs``), and that reader asks for the document
here — ``--stdout``, at the moment it judges the frontend.

Two one-line files beside it stay committed, because changing either is a decision
somebody makes:

``backend/openapi.sha256``
    the digest of the document as ``--stdout`` prints it. CI needs a path to watch:
    the frontend suite has to run when the API surface it compiles against moved,
    and the path map watches files, not the app. So the table that watched the 1.8 MB
    file watches this line instead — the changed set is the same, both move exactly
    when the document moves, and one line collides with nothing. ``--check`` fails
    when the document and the digest disagree, which means a commit changed the API
    surface without refreshing it; the fix is to run the generator above and commit
    what it writes. The comparison is worth making for its own sake too: a digest can
    only be stable if ``app.openapi()`` is a function of the tree, and that is what
    ``canonicalize`` is for.

``backend/openapi-untyped.json``
    every operation whose success response has no schema (``ok(...)`` returns ``dict``,
    which OpenAPI can only call "some object"). An operation may leave the list, none
    may join it. A new route declares what it returns (``**typed_response(Model)``, see
    ``app/api/response.py``) or this fails.

Both are checked by the backend lint job (.github/workflows/test.yml).

The document is built without starting the server or touching a database:
importing the app and asking for the schema is all ``app.openapi()`` does. Measured
on the work machine 2026-10-14: 23 s from a warm venv, nearly all of it the import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
UNTYPED_PATH = BACKEND / "openapi-untyped.json"
DIGEST_PATH = BACKEND / "openapi.sha256"

_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
#: What FastAPI writes for a route annotated ``-> dict`` (or nothing at all):
#: true of every JSON object, so it tells a client nothing.
_UNTYPED_SCHEMAS = ({}, {"type": "object", "additionalProperties": True})


def canonicalize(spec: dict[str, Any]) -> dict[str, Any]:
    """``spec`` with every path item's operations in ``_METHODS`` order.

    A route registered for several methods reaches FastAPI as a *set* of methods —
    ``installer.download_binary`` is GET and HEAD — and a set iterates in an order
    that follows the process hash seed. Left alone, two runs over unchanged code
    print the same operations in a different order, which makes the document a
    function of the process as well as of the code. It did not matter while one
    committed copy was the only thing anybody read; it matters now that every
    caller builds its own, because the same tree would print two documents.
    """
    paths = spec.get("paths", {})
    for path, item in paths.items():
        paths[path] = _ordered_operations(item)
    return spec


def _ordered_operations(item: dict[str, Any]) -> dict[str, Any]:
    """One path item: everything that is not an operation, then the operations."""
    ordered = {key: value for key, value in item.items() if key not in _METHODS}
    ordered.update({m: item[m] for m in _METHODS if m in item})
    return ordered


def build_spec() -> dict[str, Any]:
    with warnings.catch_warnings():
        # installer.download_binary is registered for GET and HEAD under one
        # name; FastAPI warns about the duplicate operationId on every build.
        warnings.filterwarnings("ignore", message="Duplicate Operation ID")
        from app.main import app

        spec = app.openapi()

    return canonicalize(spec)


def render(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def document_digest(spec: dict[str, Any]) -> str:
    """The digest of the document as ``--stdout`` prints it.

    Over the rendered text, not over ``json.dumps(spec, sort_keys=True)``: the
    reader compiles against the bytes ``--stdout`` writes, so those are the bytes
    whose identity this asserts.
    """
    return hashlib.sha256(render(spec).encode("utf-8")).hexdigest()


def _success_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON schema of the first 2xx response, or None when it is not JSON."""
    for status, response in sorted(operation.get("responses", {}).items()):
        if not status.startswith("2"):
            continue
        content = response.get("content", {})
        if "application/json" not in content:
            return None
        schema = dict(content["application/json"].get("schema", {}))
        schema.pop("title", None)
        return schema
    return None


def untyped_operations(spec: dict[str, Any]) -> list[str]:
    """``"METHOD /path"`` for every operation whose JSON success body is untyped."""
    found = []
    for path, item in spec.get("paths", {}).items():
        for method in _METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            schema = _success_schema(operation)
            if schema is not None and schema in _UNTYPED_SCHEMAS:
                found.append(f"{method.upper()} {path}")
    return sorted(found)


def read_untyped_baseline() -> list[str]:
    return json.loads(UNTYPED_PATH.read_text(encoding="utf-8"))["operations"]


def read_digest() -> str:
    """The digest recorded in the tree, or "" when there is none to compare."""
    if not DIGEST_PATH.exists():
        return ""
    return DIGEST_PATH.read_text(encoding="utf-8").strip()


def render_untyped(operations: list[str]) -> str:
    return render(
        {
            "why": (
                "Operations whose success response has no schema. The list only "
                "shrinks: declare a route's response with typed_response(...) and "
                "rerun `uv run python -m scripts.export_openapi`."
            ),
            "count": len(operations),
            "operations": operations,
        }
    )


def render_digest(digest: str) -> str:
    return f"{digest}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the document and exit — how the frontend asks for it",
    )
    parser.add_argument("--check", action="store_true", help="fail when stale")
    args = parser.parse_args(argv)

    spec = build_spec()
    if args.stdout:
        # Before the ratchet and the digest: this is the reader's whole request,
        # and a route that would widen the untyped list must still be generated
        # for — the caller judges the frontend against it either way.
        sys.stdout.write(render(spec))
        return 0

    untyped = untyped_operations(spec)
    baseline = read_untyped_baseline() if UNTYPED_PATH.exists() else untyped
    new = sorted(set(untyped) - set(baseline))
    if new:
        # The baseline is never widened from here: that is the ratchet.
        print(
            "untyped responses on new operations — declare them with "
            "**typed_response(Model) (app/api/response.py):",
            file=sys.stderr,
        )
        for op in new:
            print(f"  {op}", file=sys.stderr)
        return 1

    digest = document_digest(spec)
    if args.check:
        if not UNTYPED_PATH.exists() or UNTYPED_PATH.read_text(
            encoding="utf-8"
        ) != render_untyped(untyped):
            print(
                f"stale: {UNTYPED_PATH.name} — run `uv run python -m "
                "scripts.export_openapi` in backend/",
                file=sys.stderr,
            )
            return 1
        if read_digest() != digest:
            print(
                f"{DIGEST_PATH.name} and the document disagree: this commit changed "
                "the API surface the frontend compiles against without refreshing "
                "the digest. Run `uv run python -m scripts.export_openapi` in "
                f"backend/ and commit {DIGEST_PATH.name} — the frontend CI suite is "
                "selected by that file, so leaving it stale means the surface ships "
                "unjudged.",
                file=sys.stderr,
            )
            return 1
        return 0

    UNTYPED_PATH.write_text(render_untyped(untyped), encoding="utf-8")
    DIGEST_PATH.write_text(render_digest(digest), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
