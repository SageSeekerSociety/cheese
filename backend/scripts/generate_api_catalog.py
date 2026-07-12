#!/usr/bin/env python3
"""Generate a flattened API catalog from the legacy Kotlin OpenAPI spec.

The migration guide asks us to extract a machine-readable list of endpoints
from `cheese-backend-nt/design/API/NT-API.yml`. We keep this script inside the
Python backend repo so contract tests can depend on a stable JSON snapshot.
"""

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "options",
    "head",
}


def _default_paths() -> tuple[Path, Path]:
    backend_py_root = Path(__file__).resolve().parents[1]
    repo_root = backend_py_root.parent
    openapi_path = repo_root / "cheese-backend-nt" / "design" / "API" / "NT-API.yml"
    output_path = backend_py_root / "tests" / "contract" / "api_catalog.json"
    return openapi_path, output_path


def load_api_catalog(openapi_file: Path) -> list[dict[str, Any]]:
    """Return a sorted list of endpoint metadata from the OpenAPI document."""

    if not openapi_file.exists():
        raise FileNotFoundError(f"OpenAPI spec not found: {openapi_file}")

    document = yaml.safe_load(openapi_file.read_text()) or {}
    paths: dict[str, Any] = document.get("paths", {}) or {}

    entries: list[dict[str, Any]] = []
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, spec in operations.items():
            method_lower = str(method).lower()
            if method_lower not in SUPPORTED_METHODS:
                continue
            if not isinstance(spec, dict):
                continue

            entry = {
                "path": path,
                "method": method_lower.upper(),
                "tags": spec.get("tags", []) or [],
                "summary": spec.get("summary", "") or "",
                "operationId": spec.get("operationId"),
                "deprecated": bool(spec.get("deprecated", False)),
                "hasRequestBody": "requestBody" in spec,
                "parameters": _summarize_parameters(spec.get("parameters", [])),
                "responseCodes": sorted(
                    _collect_response_codes(spec.get("responses", {}))
                ),
            }
            entries.append(entry)

    entries.sort(key=lambda item: (item["path"], item["method"]))
    return entries


def _summarize_parameters(raw: Iterable[Any]) -> list[dict[str, Any]]:
    """Strip parameter objects down to the essentials for quick diffing."""

    summary: list[dict[str, Any]] = []
    for param in raw or []:
        if not isinstance(param, dict):
            continue
        if "$ref" in param:
            summary.append({"ref": param["$ref"]})
            continue
        summary.append(
            {
                "name": param.get("name"),
                "in": param.get("in"),
                "required": bool(param.get("required", False)),
            }
        )
    return summary


def _collect_response_codes(responses: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for status_code in responses:
        codes.append(str(status_code))
    return codes


def parse_args() -> argparse.Namespace:
    default_openapi, default_output = _default_paths()

    parser = argparse.ArgumentParser(
        description="Generate API catalog JSON from NT OpenAPI spec."
    )
    parser.add_argument(
        "--openapi",
        type=Path,
        default=default_openapi,
        help="Path to NT-API.yml (defaults to cheese-backend-nt/design/API/NT-API.yml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Where to write the generated catalog JSON (defaults to tests/contract/api_catalog.json)",  # noqa: E501
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    entries = load_api_catalog(args.openapi)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    args.output.write_text(payload)
    rel_output = args.output
    try:
        rel_output = args.output.relative_to(Path.cwd())
    except ValueError:
        pass
    print(f"Wrote {len(entries)} endpoints to {rel_output}")


if __name__ == "__main__":
    main()
