"""Handwritten route navigation notes; never application authorization config.

Coverage and format are checked here. Whether a note describes the right
authorization entry or business owner is a source-review responsibility.
"""

import re
from pathlib import Path

import yaml

DEFAULT_SIDECAR = Path(__file__).resolve().parents[1] / "tests/contract/route_index.yaml"


class _UniqueLoader(yaml.SafeLoader):
    """YAML must not silently overwrite a handwritten declaration."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ValueError("route annotations mapping key must be scalar") from exc
        if duplicate:
            raise ValueError(f"duplicate YAML mapping key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _key(protocol, method, path):
    if protocol not in ("http", "ws"):
        raise ValueError(f"route annotations protocol invalid: {protocol!r}")
    method = _text(method, "route annotations method")
    path = _text(path, "route annotations path")
    if not path.startswith("/"):
        raise ValueError(f"route annotations path must start with '/': {path!r}")
    if protocol == "ws" and method != "WS":
        raise ValueError("WebSocket route annotations require method WS")
    if protocol == "http" and (method == "WS" or not re.fullmatch(r"[A-Z]+", method)):
        raise ValueError(f"HTTP route annotations method invalid: {method!r}")
    return protocol, method, path


def _catalog(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"route annotations {label} must be a nonempty mapping")
    for name, note in value.items():
        _text(name, f"route annotations {label} id")
        _text(note, f"route annotations {label} note for {name!r}")
    return value


def read_annotations(path: str | Path) -> dict[tuple[str, str, str], dict]:
    """Read explicit address keys and require registered, nonempty notes.

    Shared auth/owner text lives in the handwritten catalogs. Each address is
    explicit; there are no path patterns, defaults or automatic owner guesses.
    """
    try:
        document = yaml.load(
            Path(path).read_text(encoding="utf-8"), Loader=_UniqueLoader
        )
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"route annotations unreadable: {exc}") from exc
    if (
        not isinstance(document, dict)
        or type(document.get("version")) is not int
        or document["version"] != 1
    ):
        raise ValueError("route annotations version must be 1")
    if set(document) != {"version", "auth_entries", "owners", "routes"}:
        raise ValueError("route annotations require version/auth_entries/owners/routes")
    auth_entries = _catalog(document["auth_entries"], "auth_entries")
    owners = _catalog(document["owners"], "owners")
    rows = document["routes"]
    if not isinstance(rows, list):
        raise ValueError("route annotations routes must be a list")
    result = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {
            "protocol",
            "method",
            "path",
            "auth",
            "owner",
        }:
            raise ValueError(
                f"route annotation {position} requires protocol/method/path/auth/owner"
            )
        key = _key(row["protocol"], row["method"], row["path"])
        if key in result:
            raise ValueError(f"duplicate route annotation: {key!r}")
        auth = _text(row["auth"], f"route annotation {key!r} auth")
        owner = _text(row["owner"], f"route annotation {key!r} owner")
        if auth not in auth_entries:
            raise ValueError(f"unknown route auth entry {auth!r}: {key!r}")
        if owner not in owners:
            raise ValueError(f"unknown route owner {owner!r}: {key!r}")
        result[key] = {"auth": auth_entries[auth], "owner": owners[owner]}
    return result


def check_annotation_coverage(rows: list[dict], path: str | Path) -> int:
    """All snapshot methods, including WS, are covered and no addresses orphan.

    Only coverage uses a set. The caller compares the original effective leaf
    sequence first, retaining duplicate registrations and FrozenPair identity.
    """
    annotations = read_annotations(path)
    keys = set()
    for row in rows:
        protocol, methods = row.get("protocol"), row.get("methods")
        if not isinstance(methods, list) or any(
            not isinstance(m, str) for m in methods
        ):
            raise ValueError("route snapshot methods must be a list of strings")
        if protocol == "ws":
            if methods:
                raise ValueError("WebSocket route snapshot must have no HTTP methods")
            methods = ["WS"]
        elif not methods:
            raise ValueError("HTTP route snapshot must have at least one method")
        for method in methods:
            keys.add(_key(protocol, method, row.get("path")))
    missing = sorted(keys - annotations.keys())
    orphan = sorted(annotations.keys() - keys)
    if missing or orphan:
        raise ValueError(
            f"route annotations coverage mismatch: missing={missing!r}; orphan={orphan!r}"
        )
    return len(keys)
