"""The API's OpenAPI document, committed, and the routes that still answer untyped.

    uv run python -m scripts.export_openapi            rewrite both files
    uv run python -m scripts.export_openapi --check    exit 1 if either is stale

Two files, both written by this script and nothing else:

* ``backend/openapi.json`` — ``app.openapi()`` as the running app serves it. The
  frontend generates its types from this file (``pnpm run gen:api-types`` in
  ``frontend/``, written to ``frontend/src/api/openapi.d.ts``), so a route change
  and the frontend types that follow it land in the same diff, and review sees both.
* ``backend/openapi-untyped.json`` — every operation whose success response has no
  schema (``ok(...)`` returns ``dict``, which OpenAPI can only call "some object").
  It is a ratchet: an operation may leave the list, none may join it. A new route
  declares what it returns (``**typed_response(Model)``, see ``app/api/response.py``)
  or ``--check`` fails — which the backend lint job runs (.github/workflows/test.yml).

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


def build_spec() -> dict[str, Any]:
    with warnings.catch_warnings():
        # installer.download_binary is registered for GET and HEAD under one
        # name; FastAPI warns about the duplicate operationId on every build.
        warnings.filterwarnings("ignore", message="Duplicate Operation ID")
        from app.main import app

        return app.openapi()


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
    parser.add_argument("--check", action="store_true", help="fail when stale")
    args = parser.parse_args(argv)

    spec = build_spec()
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
        stale = []
        if not SPEC_PATH.exists() or SPEC_PATH.read_text(encoding="utf-8") != render(
            spec
        ):
            stale.append(SPEC_PATH.name)
        if not UNTYPED_PATH.exists() or UNTYPED_PATH.read_text(
            encoding="utf-8"
        ) != render_untyped(untyped):
            stale.append(UNTYPED_PATH.name)
        if stale:
            print(
                f"stale: {', '.join(stale)} — run `uv run python -m "
                "scripts.export_openapi` in backend/, then `pnpm run gen:api-types` "
                "in frontend/",
                file=sys.stderr,
            )
            return 1
        return 0

    SPEC_PATH.write_text(render(spec), encoding="utf-8")
    UNTYPED_PATH.write_text(render_untyped(untyped), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
