"""The API's OpenAPI document, generated, and the routes that still answer untyped.

    uv run python -m scripts.export_openapi           write both files
    uv run python -m scripts.export_openapi --check   regenerate the document, then
                                                      exit 1 if the ratchet is stale
    uv run python -m scripts.export_openapi --schema  write backend/openapi.json only

Two files, both written by this script and nothing else:

* ``backend/openapi.json`` — ``app.openapi()`` as the running app serves it. It is
  generated, not committed (``backend/.gitignore``): as a tracked file it changed
  with every route, so two branches that each touched a route collided in it
  without either having a decision in it. Everything that reads it generates it
  first — the frontend type check runs ``--schema`` before it compiles
  (``frontend/scripts/gen-api-types.mjs``), and ``be:openapi`` in
  ``backend/Taskfile.yml`` is the same generation for anything else.
* ``backend/openapi-untyped.json`` — every operation whose success response has no
  schema (``ok(...)`` returns ``dict``, which OpenAPI can only call "some object").
  It is committed, and it is a ratchet: an operation may leave the list, none may
  join it. A new route declares what it returns (``**typed_response(Model)``, see
  ``app/api/response.py``) or ``--check`` fails — which the backend lint job runs
  (.github/workflows/test.yml). ``--check`` and ``--schema`` are mutually
  exclusive, and ``--schema`` never touches the ratchet, so a consumer that only
  needs the document cannot silently rebaseline it.

The document is built without starting the server or touching a database:
importing the app and asking for the schema is all ``app.openapi()`` does.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
SPEC_PATH = BACKEND / "openapi.json"
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
    write the same operations in a different order, so the generated document
    (and the types the frontend generates from it) would differ run to run.
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


def write_schema(spec: dict[str, Any]) -> None:
    SPEC_PATH.write_text(render(spec), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    # Mutually exclusive: `--schema` wants the document and nothing else, while
    # `--check` exists to judge the tracked ratchet. A caller that passed both
    # would be asking to skip the check it asked for, so the pair is refused.
    mode.add_argument(
        "--check",
        action="store_true",
        help="regenerate the document, then fail when the ratchet is stale",
    )
    mode.add_argument(
        "--schema",
        action="store_true",
        help="write only backend/openapi.json (the untyped ratchet is left alone)",
    )
    args = parser.parse_args(argv)

    spec = build_spec()
    # Every mode writes the document: it is what every reader needs, and --check
    # is told to leave one behind rather than skip it.
    write_schema(spec)

    if args.schema:
        # The consumer path: the frontend type check and the generation task run
        # this to get the document they read. The untyped baseline is a tracked
        # ratchet the backend lint job holds, and rewriting it here would let
        # generation silently rebaseline it — so this mode leaves it alone.
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
        # The document is generated and not committed, so only the ratchet is
        # judged here: it is what the backend lint job gates on.
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
