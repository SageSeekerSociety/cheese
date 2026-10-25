"""The API's OpenAPI document, printed to whoever asks for it, and the
untyped-route ratchet.

    uv run python -m scripts.export_openapi --stdout   print the document
    uv run python -m scripts.export_openapi            refresh the ratchet
    uv run python -m scripts.export_openapi --check    exit 1 if it is stale

The document is ``app.openapi()`` as the running app serves it, and nothing derived
from it is committed. It used to be ``backend/openapi.json``, and then a one-line
digest of it, ``backend/openapi.sha256``: either way every PR that moved the API
surface rewrote a file nobody had a decision in, and any two of them collided there
— on 2026-10-17 all three merge-queue ejections involved the digest, and one PR was
ejected three times for that file alone. Its readers ask for the document here,
``--stdout``, when they need it:

- the frontend, which needs the types it compiles against
  (``frontend/scripts/gen-api-types.mjs``);
- the ``api`` job of Required CI (.github/workflows/required-ci.yml), which prints
  the document at the merge base and at the change and runs the frontend suite
  when the two differ. That is what the digest was committed for: the path map
  watches files, not the app, so it needed a file that moved exactly when the
  document did. Comparing the two documents asks the question directly. The
  same job hands both documents to ``.github/scripts/api_compat.py``, which
  fails a change that breaks pages built against the base unless it raised
  ``backend/api-generation``.

Two runs over the same tree must print the same bytes, or that comparison would
call every change an API change: ``canonicalize`` is what makes the document a
function of the tree alone.

``backend/openapi-untyped.json`` stays committed, because changing it is a
decision: every operation whose success response has no schema (``ok(...)`` returns
``dict``, which OpenAPI can only call "some object"). An operation may leave the
list, none may join it. A new route declares what it returns
(``**typed_response(Model)``, see ``app/api/response.py``) or ``--check`` fails. The
backend lint job runs that check (.github/workflows/test.yml).

The document is built without starting the server or touching a database:
importing the app and asking for the schema is all ``app.openapi()`` does. Measured
on the work machine 2026-10-14: 23 s from a warm venv, nearly all of it the import.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
UNTYPED_PATH = BACKEND / "openapi-untyped.json"

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
    caller builds its own, because the same tree would print two documents — and
    the ``api`` job, which compares the document at two commits, would read the
    reshuffle as an API change.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the document and exit — how its readers ask for it",
    )
    parser.add_argument("--check", action="store_true", help="fail when stale")
    args = parser.parse_args(argv)

    spec = build_spec()
    if args.stdout:
        # Before the ratchet: this is the reader's whole request, and a route
        # that would widen the untyped list must still be generated for — the
        # caller judges the frontend against it either way.
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
        return 0

    UNTYPED_PATH.write_text(render_untyped(untyped), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
