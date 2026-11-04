"""What a forge request would do to pull requests and branches, read the way the
forge reads it.

The forge proxy (`forge_proxy.py`) refuses an agent two things: merging a pull
request, and closing one that is a delivery waiting on a person (deleting its
branch, or leaving it with nothing over its base, closes it too). Matching
request bytes against patterns misses every request that says the same thing
differently — a path under the forge's own base (`/forge/api/v1/repos/…`), a
doubled slash, the `/repositories/{id}` alias, a form-encoded body, a JSON
string with `\\u` escapes, an aliased GraphQL field, a gzip stream of several
members. So the request is first normalized and parsed the way the forge would:
the path decoded, its dot segments and repeated slashes resolved, and read from
its `/repos/{owner}/{repo}` or `/repositories/{id}` part on, whatever stands in
front of it; a method override honoured; the body read by its content type;
a GraphQL request parsed into its operations and their top-level fields; a
push's pkt-lines read from the fully decompressed body. Whatever could close or
delete and cannot be read is reported as unreadable, and the proxy refuses it
while any delivery waits (`ForgeWrite.unreadable`).

Shipped beside `forge_proxy.py` to execution machines (`RELEASE_FILES`), so it
uses the standard library only.
"""

from __future__ import annotations

import json
import posixpath
import re
import urllib.parse
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field

WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Mutations that can close a pull request or touch a branch's ref.
CLOSING_FIELDS = frozenset(
    {
        "closePullRequest",
        "closeIssue",
        "updatePullRequest",
        "updateIssue",
        "deleteRef",
        "updateRef",
        "updateRefs",
    }
)
#: The two that only close when asked to (`state: CLOSED`).
UPDATE_FIELDS = frozenset({"updatePullRequest", "updateIssue"})
MERGE_FIELDS = frozenset({"mergePullRequest", "enablePullRequestAutoMerge"})
#: A GitHub global node id: `PR_kwDO…` / `I_kwDO…` / `REF_kwDO…`, or the
#: legacy base64 form.
NODE_ID_RE = re.compile(
    r"^(?:[A-Z][A-Za-z]{0,5}_[A-Za-z0-9_-]{6,}|MD[A-Za-z0-9+/=]{8,})$"
)
_REPO_RE = re.compile(r"/(?:repos/[^/]+/[^/]+|repositories/\d+)/(.+)$")
_REPO_ROOT_RE = re.compile(r"/(?:repos/[^/]+/[^/]+|repositories/\d+)$")
_ZERO = re.compile(r"^0+$")
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_HEADS = "refs/heads/"


@dataclass(frozen=True)
class ForgeWrite:
    """What one request would change. Every field is a candidate; the proxy
    decides by comparing them with the deliveries that wait."""

    #: Pull request (or issue) numbers it would close, or edit in a way that
    #: could not be read.
    numbers: frozenset[int] = frozenset()
    #: Branch resources it writes over REST, as the path after the repository
    #: (`git/refs/heads/{b}`, `branches/{b}`, `branches/{b}/rename`).
    ref_paths: frozenset[str] = frozenset()
    #: Branch names a GraphQL mutation names (`refs/heads/` stripped).
    names: frozenset[str] = frozenset()
    #: GitHub node ids a closing GraphQL mutation names, to resolve.
    node_ids: frozenset[str] = frozenset()
    #: (branch, new sha) of each branch a push updates; an all-zero sha
    #: deletes it.
    pushes: tuple[tuple[str, str], ...] = ()
    #: It merges a pull request, or branches outside a pull request.
    merge: bool = False
    #: It deletes the repository.
    deletes_repository: bool = False
    #: It could close or delete and could not be read.
    unreadable: bool = False

    def any(self) -> bool:
        return bool(
            self.numbers
            or self.ref_paths
            or self.names
            or self.node_ids
            or self.pushes
            or self.merge
            or self.deletes_repository
            or self.unreadable
        )


def read(method: str, path: str, headers: Mapping[str, str], body: bytes) -> ForgeWrite:
    """What `method path` with `headers` and `body` would do to pull requests
    and branches. Reads nothing over the network."""
    lower = {k.lower(): v for k, v in headers.items()}
    method = (
        lower.get("x-http-method-override") or lower.get("x-http-method") or method
    ).upper()
    clean = normalized_path(path)
    if clean.endswith("/git-receive-pack"):
        return _read_push(lower, body) if method == "POST" else ForgeWrite()
    if clean.endswith("/graphql"):
        return _read_graphql(body) if method == "POST" else ForgeWrite()
    if method not in WRITES:
        return ForgeWrite()
    if method == "DELETE" and _REPO_ROOT_RE.search(clean):
        return ForgeWrite(deletes_repository=True)
    match = _REPO_RE.search(clean)
    if not match:
        return ForgeWrite()
    rest = match.group(1)
    if rest.startswith(("git/refs/", "branches/")):
        return ForgeWrite(ref_paths=frozenset({rest}))
    number = re.fullmatch(r"(?:pulls|issues)/(\d+)", rest)
    if number and method == "DELETE":
        return ForgeWrite(numbers=frozenset({int(number.group(1))}))
    if number:
        pairs = _fields(lower.get("content-type", ""), body)
        if pairs is None or str(_decoded_field(pairs, "state")).lower() == "closed":
            return ForgeWrite(numbers=frozenset({int(number.group(1))}))
        return ForgeWrite()
    if re.fullmatch(r"pulls/\d+/merge", rest) and method in ("PUT", "POST"):
        return ForgeWrite(merge=True)
    # Merging one branch into another without a pull request (GitHub's
    # `POST /repos/{o}/{r}/merges`) is a merge too.
    if rest == "merges" and method == "POST":
        return ForgeWrite(merge=True)
    return ForgeWrite()


def normalized_path(path: str) -> str:
    """The request path as the forge routes it: percent-decoded, repeated
    slashes collapsed, dot segments resolved, no trailing slash."""
    # A leading `//` is part of the path, not an authority: only an
    # absolute-form target names a host.
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path):
        raw = urllib.parse.urlsplit(path).path or "/"
    else:
        raw = path.split("?", 1)[0].split("#", 1)[0] or "/"
    # Decoded until it stops changing: a router may decode a part again
    # (`%252F` → `%2F` → `/`), so the reading that finds the most is the one
    # that counts.
    decoded = raw
    for _ in range(8):
        again = urllib.parse.unquote(decoded)
        if again == decoded:
            break
        decoded = again
    collapsed = re.sub(r"/{2,}", "/", decoded)
    resolved = posixpath.normpath(collapsed)
    return "/" + resolved.lstrip("/") if resolved != "." else "/"


def _fields(content_type: str, body: bytes) -> list[tuple[str, object]] | None:
    """The body's top-level fields as (key, value) pairs in the order sent,
    read by its content type; None when it cannot be read. An empty body has
    no fields."""
    if not body.strip():
        return []
    kind = content_type.split(";", 1)[0].strip().lower()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if kind == "application/x-www-form-urlencoded":
        return list(urllib.parse.parse_qsl(text, keep_blank_values=True))
    try:
        value = json.loads(text, object_pairs_hook=_Pairs)
    except ValueError:
        if kind in ("", "text/plain"):
            parsed = urllib.parse.parse_qsl(text, strict_parsing=True)
            return list(parsed) if parsed else None
        return None
    return list(value) if isinstance(value, _Pairs) else None


class _Pairs(list):
    """A JSON object kept as its (key, value) pairs, duplicates included."""


def _decoded_field(pairs: list[tuple[str, object]], name: str) -> object:
    """The value a forge's decoder gives field `name`: Go's encoding/json
    matches keys case-insensitively (with Unicode folding, so `ſtate` is
    `state`) and lets a later key overwrite an earlier one."""
    value: object = None
    for key, item in pairs:
        if key.casefold() == name.casefold():
            value = item
    return value


# --- git push -----------------------------------------------------------------


def _decompressed(encoding: str, body: bytes) -> bytes | None:
    """The whole body, every gzip member of it; None when it cannot be
    decompressed."""
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding not in ("gzip", "x-gzip", "deflate"):
        return None
    out = bytearray()
    data = body
    try:
        while data:
            wbits = 16 + zlib.MAX_WBITS if encoding != "deflate" else zlib.MAX_WBITS
            stream = zlib.decompressobj(wbits)
            out += stream.decompress(data)
            out += stream.flush()
            if not stream.eof:
                return None
            data = stream.unused_data.lstrip(b"\0")
            if encoding == "deflate":
                break
    except zlib.error:
        return None
    return bytes(out)


def _read_push(headers: Mapping[str, str], body: bytes) -> ForgeWrite:
    """The branches a push updates. Its command list is the pkt-lines up to
    the first flush; a signed push puts the commands inside its certificate,
    which is read the same way."""
    data = _decompressed(headers.get("content-encoding", ""), body)
    if data is None:
        return ForgeWrite(unreadable=True)
    pushes: list[tuple[str, str]] = []
    at = 0
    while True:
        if at + 4 > len(data):
            return ForgeWrite(unreadable=True)
        try:
            size = int(data[at : at + 4], 16)
        except ValueError:
            return ForgeWrite(unreadable=True)
        if size == 0:
            break
        if size < 4 or at + size > len(data):
            return ForgeWrite(unreadable=True)
        line = data[at + 4 : at + size].split(b"\0", 1)[0].rstrip(b"\n")
        at += size
        parts = line.decode("utf-8", "replace").split(" ")
        if len(parts) == 3 and _SHA.match(parts[0]) and _SHA.match(parts[1]):
            ref = parts[2]
            if ref.startswith(_HEADS):
                pushes.append((ref[len(_HEADS) :], parts[1]))
    return ForgeWrite(pushes=tuple(pushes))


def is_deletion(sha: str) -> bool:
    return bool(_ZERO.match(sha))


# --- GraphQL ------------------------------------------------------------------


def _read_graphql(body: bytes) -> ForgeWrite:
    try:
        request = json.loads(body)
    except ValueError:
        return ForgeWrite(unreadable=True)
    if not isinstance(request, dict) or not isinstance(request.get("query"), str):
        return ForgeWrite(unreadable=True)
    try:
        operations = parse_operations(request["query"])
    except GraphQLSyntaxError:
        return ForgeWrite(unreadable=True)
    wanted = request.get("operationName")
    mutations = [
        op
        for op in operations
        if op.kind == "mutation" and (not wanted or op.name == wanted)
    ]
    provided = request.get("variables")
    if isinstance(provided, str):
        # Some servers take the variables as a JSON string.
        try:
            provided = json.loads(provided) if provided.strip() else {}
        except ValueError:
            return ForgeWrite(unreadable=True)
    variables = _strings(provided)
    merge = False
    unreadable = False
    node_ids: set[str] = set()
    names: set[str] = set()
    for op in mutations:
        unreadable = unreadable or op.spreads
        # A variable's value is what was sent, or else its declared default.
        known = variables + op.defaults
        for name, args in op.fields:
            if name in MERGE_FIELDS:
                merge = True
            if name not in CLOSING_FIELDS:
                continue
            values = args + known
            if name in UPDATE_FIELDS and "CLOSED" not in values:
                continue
            for value in values:
                if NODE_ID_RE.match(value):
                    node_ids.add(value)
                names.add(value.removeprefix(_HEADS))
            if not values:
                unreadable = True
    return ForgeWrite(
        node_ids=frozenset(node_ids),
        names=frozenset(names),
        merge=merge,
        unreadable=unreadable,
    )


def _strings(value) -> list[str]:
    """Every string (and enum-like word) in a JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


class GraphQLSyntaxError(ValueError):
    """The document is not one this reader can read. Never shown to anyone:
    the request it came with is refused as unreadable."""


@dataclass
class Operation:
    kind: str
    name: str | None
    #: (field name, the string and enum values in its arguments), top level.
    fields: list[tuple[str, list[str]]] = field(default_factory=list)
    #: A named fragment is spread at the top level: its fields are not read.
    spreads: bool = False
    #: The string and enum values in its variable definitions (defaults).
    defaults: list[str] = field(default_factory=list)


_TOKEN = re.compile(
    r'(?P<block>"""(?:\\"""|[^"]|"(?!""))*""")'
    r'|(?P<string>"(?:\\.|[^"\\\n])*")'
    r"|(?P<name>[_A-Za-z][_0-9A-Za-z]*)"
    r"|(?P<number>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<spread>\.\.\.)"
    r"|(?P<punct>[!$&():=@\[\]{|}])"
    r"|(?P<skip>[\s,﻿]+|#[^\n\r]*)"
)


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    at = 0
    while at < len(text):
        match = _TOKEN.match(text, at)
        if not match:
            raise GraphQLSyntaxError
        at = match.end()
        kind = match.lastgroup or ""
        if kind == "skip":
            continue
        value = match.group()
        if kind == "string":
            value = json.loads(value)
        elif kind == "block":
            value = value[3:-3].replace('\\"""', '"""')
        out.append((kind, value))
    return out


def parse_operations(text: str) -> list[Operation]:
    """The operations of a GraphQL document, each with its top-level fields
    and the string and enum values in their arguments."""
    tokens = _tokens(text)
    at = 0
    ops: list[Operation] = []

    def peek(offset: int = 0) -> tuple[str, str]:
        index = at + offset
        return tokens[index] if index < len(tokens) else ("eof", "")

    def take(value: str | None = None) -> tuple[str, str]:
        nonlocal at
        token = peek()
        if token[0] == "eof" or (value is not None and token[1] != value):
            raise GraphQLSyntaxError
        at += 1
        return token

    def balanced(open_: str, close: str) -> list[str]:
        """Skip a bracketed group; return the strings and words inside."""
        take(open_)
        depth, values = 1, []
        while depth:
            kind, value = take()
            if value == open_ and kind == "punct":
                depth += 1
            elif value == close and kind == "punct":
                depth -= 1
            elif kind in ("string", "block", "name"):
                values.append(value)
        return values

    def directives() -> None:
        while peek()[1] == "@":
            take("@")
            take()
            if peek()[1] == "(":
                balanced("(", ")")

    def selection_set(op: Operation, top: bool) -> None:
        take("{")
        while peek()[1] != "}":
            if peek()[0] == "spread":
                take()
                if peek()[1] == "on" or peek()[1] == "{" or peek()[1] == "@":
                    if peek()[1] == "on":
                        take("on")
                        take()
                    directives()
                    selection_set(op, top)
                else:
                    take()
                    directives()
                    if top:
                        op.spreads = True
                continue
            kind, name = take()
            if kind != "name":
                raise GraphQLSyntaxError
            if peek()[1] == ":":
                take(":")
                kind, name = take()
                if kind != "name":
                    raise GraphQLSyntaxError
            args = balanced("(", ")") if peek()[1] == "(" else []
            directives()
            if peek()[1] == "{":
                selection_set(op, False)
            if top:
                op.fields.append((name, args))
        take("}")

    while peek()[0] != "eof":
        kind, value = peek()
        if value == "{":
            op = Operation("query", None)
            selection_set(op, True)
            ops.append(op)
            continue
        if kind != "name":
            raise GraphQLSyntaxError
        if value == "fragment":
            take()
            take()
            take("on")
            take()
            directives()
            selection_set(Operation("fragment", None), False)
            continue
        if value not in ("query", "mutation", "subscription"):
            raise GraphQLSyntaxError
        take()
        name = take()[1] if peek()[0] == "name" else None
        defaults = balanced("(", ")") if peek()[1] == "(" else []
        directives()
        op = Operation(value, name, defaults=defaults)
        selection_set(op, True)
        ops.append(op)
    return ops
