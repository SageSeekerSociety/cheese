"""Whether a change to the API breaks a page built against the old one, and if
so, whether it raised the API generation.

    python3 api_compat.py BASE.json HEAD.json BASE_GENERATION HEAD_GENERATION

Prints every breaking change between the two OpenAPI documents and exits 1 when
there is one and the generation did not rise. A page left open across a deploy
keeps the frontend it loaded; the backend refuses it only when it was built for
a lower generation (backend/app/core/api_generation.py), so a breaking change
that leaves the number alone leaves those pages talking to an API that no
longer takes what they send.

Breaking, for a page built against BASE:
- an operation (path and method) it calls is gone;
- a request field or parameter it sends is gone or renamed, takes fewer types
  than before, or lost an enum value;
- a request field, parameter or body it does not send became required;
- a response field it reads is gone or renamed, may now be absent or carry a
  type it never saw (a field that became nullable, among others), or lost an
  enum value.

Additions are not: a new operation, a new optional field, a response field
that was not there before, a request that accepts more. A response whose
schema says nothing (``-> dict``, backend/openapi-untyped.json) has no fields
to compare, so a change inside it goes unseen here; that ratchet only shrinks.

Fields are compared by where they sit, ``$ref``s resolved, so renaming a model
alone breaks nothing (except a kind in a union whose kinds carry no
discriminator, which is named after its model). Variants of
``anyOf``/``oneOf``/``allOf`` are merged: their fields pooled, a field's types the union over the variants, except
that a union of several object shapes (kinds of a quote, of a change) keeps
each kind's fields apart: a new kind breaks nothing, while a new required
field on a kind an old page sends does, and so does a kind removed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


class Field:
    __slots__ = ("types", "enum", "required")

    def __init__(self) -> None:
        self.types: set[str] = set()
        self.enum: set[str] | None = None
        self.required = False


def _resolve(spec: dict, schema: dict) -> tuple[dict, str | None]:
    ref = schema.get("$ref")
    if not ref:
        return schema, None
    node: object = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]  # type: ignore[index]
    return node, ref  # type: ignore[return-value]


def fields(spec: dict, schema: dict | None) -> dict[str, Field]:
    """Every place in ``schema`` a value sits, as ``{pointer: Field}``.

    ``""`` is the value itself; ``.name`` a property; ``[]`` an array's items;
    ``{}`` a map's values."""
    out: dict[str, Field] = {}
    if schema is not None:
        _walk(spec, schema, "", out, required=True, seen=())
    return out


def _kind(spec: dict, variant: dict, index: int) -> str | None:
    """What names one object shape among several in a union — its
    discriminator value, else its model's name — or None when it is not an
    object (``string | integer`` is one value of several types, not shapes)."""
    schema, ref = _resolve(spec, variant)
    properties = schema.get("properties")
    if not properties:
        return None
    for name, child in properties.items():
        child, _ = _resolve(spec, child)
        if "const" in child:
            return f"{name}={json.dumps(child['const'])}"
        if len(child.get("enum", ())) == 1:
            return f"{name}={json.dumps(child['enum'][0])}"
    if ref:
        return ref.rsplit("/", 1)[-1].removesuffix("-Input").removesuffix("-Output")
    return str(index)


def _walk(spec, schema, at, out, *, required, seen):
    schema, ref = _resolve(spec, schema)
    field = out.setdefault(at, Field())
    field.required = field.required or required
    if ref in seen:
        # A schema that contains itself (a tree): its fields are already
        # walked one level up.
        field.types.add("object")
        return
    if ref:
        seen = (*seen, ref)
    alternatives = [*schema.get("anyOf", ()), *schema.get("oneOf", ())]
    variants = [*alternatives, *schema.get("allOf", ())]
    # A union of several object shapes keeps each shape's fields apart, under
    # `<kind>`: a new kind is a new place, and an old kind keeps its own
    # required fields. `X | None` is one shape and stays where it is.
    kinds = [_kind(spec, v, i) for i, v in enumerate(alternatives)]
    several = sum(k is not None for k in kinds) > 1
    for variant, kind in zip(alternatives, kinds, strict=True):
        place = f"{at}<{kind}>" if several and kind is not None else at
        _walk(spec, variant, place, out, required=False, seen=seen)
    for variant in schema.get("allOf", ()):
        _walk(spec, variant, at, out, required=False, seen=seen)
    kind = schema.get("type")
    if kind is not None:
        field.types.update(kind if isinstance(kind, list) else [kind])
    elif not variants and ("properties" in schema or "additionalProperties" in schema):
        field.types.add("object")
    if "enum" in schema:
        field.enum = (field.enum or set()) | {json.dumps(v) for v in schema["enum"]}
    if "const" in schema:
        field.enum = (field.enum or set()) | {json.dumps(schema["const"])}
    needed = set(schema.get("required", ()))
    for name, child in schema.get("properties", {}).items():
        _walk(spec, child, f"{at}.{name}", out, required=name in needed, seen=seen)
    if "items" in schema and isinstance(schema["items"], dict):
        _walk(spec, schema["items"], f"{at}[]", out, required=False, seen=seen)
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict) and extra:
        _walk(spec, extra, f"{at}{{}}", out, required=False, seen=seen)


def _operations(spec: dict) -> dict[str, dict]:
    return {
        f"{method.upper()} {path}": item[method]
        for path, item in spec.get("paths", {}).items()
        for method in METHODS
        if method in item
    }


def _request(spec: dict, operation: dict) -> dict[str, Field]:
    """What a caller sends: each parameter, and the body's fields."""
    out: dict[str, Field] = {}
    for parameter in operation.get("parameters", ()):
        parameter, _ = _resolve(spec, parameter)
        name = f"{parameter['in']} {parameter['name']}"
        for at, field in fields(spec, parameter.get("schema", {})).items():
            if at == "":
                field.required = bool(parameter.get("required"))
            out[f"{name}{at}"] = field
    body, _ = _resolve(spec, operation.get("requestBody", {}))
    for content in body.get("content", {}).values():
        for at, field in fields(spec, content.get("schema")).items():
            if at == "":
                field.required = bool(body.get("required"))
            out[f"body{at}"] = field
        break
    return out


def _response(spec: dict, operation: dict) -> dict[str, Field]:
    """What a caller reads: the first 2xx response's JSON body."""
    for status, response in sorted(operation.get("responses", {}).items()):
        if not status.startswith("2"):
            continue
        response, _ = _resolve(spec, response)
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        return {f"response{at}": field for at, field in fields(spec, schema).items()}
    return {}


def breaking_changes(base: dict, head: dict) -> list[str]:
    found: list[str] = []
    head_operations = _operations(head)
    for name, old in _operations(base).items():
        new = head_operations.get(name)
        if new is None:
            found.append(f"{name}: removed")
            continue
        found += [f"{name}: {what}" for what in _request_breaks(_request(base, old), _request(head, new))]
        found += [f"{name}: {what}" for what in _response_breaks(_response(base, old), _response(head, new))]
    return found


def _request_breaks(old: dict[str, Field], new: dict[str, Field]) -> list[str]:
    found = []
    for at, was in old.items():
        now = new.get(at)
        if now is None:
            found.append(f"request {at} removed")
            continue
        if was.types and now.types and was.types - now.types:
            found.append(f"request {at} no longer takes {sorted(was.types - now.types)}")
        if was.enum is not None and now.enum is not None and was.enum - now.enum:
            found.append(f"request {at} no longer takes {sorted(was.enum - now.enum)}")
        if now.required and not was.required:
            found.append(f"request {at} became required")
    for at, now in new.items():
        # A required field inside an object that is itself new is not one an
        # old caller fails to send: it sends no such object.
        parent = _parent(at)
        if at not in old and now.required and (parent is None or parent in old):
            found.append(f"request {at} added as required")
    return found


def _parent(at: str) -> str | None:
    """The pointer of the object holding ``at``; None for a parameter or the
    body itself."""
    cut = max(at.rfind("."), at.rfind("[]"), at.rfind("{}"), at.rfind("<"))
    return at[:cut] if cut > 0 else None


def _response_breaks(old: dict[str, Field], new: dict[str, Field]) -> list[str]:
    found = []
    for at, was in old.items():
        now = new.get(at)
        if now is None:
            found.append(f"{at} removed")
            continue
        if was.types and now.types and now.types - was.types:
            found.append(f"{at} may now be {sorted(now.types - was.types)}")
        if was.required and not now.required:
            found.append(f"{at} may now be absent")
        if was.enum is not None and now.enum is not None and was.enum - now.enum:
            found.append(f"{at} lost {sorted(was.enum - now.enum)}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    base, head = (json.loads(Path(path).read_text(encoding="utf-8")) for path in argv[1:3])
    base_generation, head_generation = int(argv[3]), int(argv[4])
    found = breaking_changes(base, head)
    if not found:
        print("No change here breaks a page built against the base's API.")
        return 0
    print(f"{len(found)} change(s) break a page built against the base's API:")
    for line in found:
        print(f"  {line}")
    if head_generation > base_generation:
        print(f"The API generation rose ({base_generation} -> {head_generation}): open pages reload.")
        return 0
    print(
        f"::error::A breaking API change must raise backend/api-generation "
        f"(it is {head_generation}, the base has {base_generation}), so pages "
        "still open on the old frontend reload instead of sending the old shape."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
