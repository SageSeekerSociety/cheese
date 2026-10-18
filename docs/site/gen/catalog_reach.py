"""Which components the preview site `/demo/catalog` shows, read from its code.

`docs/site/gen/components.py` asks this for the components page. "In the
catalog" is read from the code, not from a label: `src/views/demo/catalog.ts`,
or a volume it imports and spreads into `CATALOG` (a flat `catalog*.ts` beside
it, or a shard under `catalog/`; `*Fixtures.ts` are data and not read), imports
the `.vue` and uses it as an entry's `component:`. Imports and uses are read
with `//` and `/* */` comments stripped, so a commented-out entry shows
nothing. Still a regex reading: the spread is recognised anywhere in the
importing file, not proven to land in the `CATALOG` array itself.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

#: The preview site: its own components are not subjects, its `.ts` files are
#: where the catalog lives.
DEMO_DIR = "frontend/src/views/demo/"

#: A spec file puts nothing on the site, whatever it imports.
SPEC = re.compile(r"\.(spec|test)\.(ts|js)$")

#: A catalog volume beside the entry point: `catalogAccept.ts`, `catalogBase.ts`, ...
VOLUME = re.compile(r"^catalog[A-Za-z0-9_]*\.ts$")

#: ...or one level down, grouped by scene. The names there are scene words
#: (`room`, `panel-views`), not `catalog*` ones, so the shape above cannot be
#: the test; what makes a file a shard is being under this directory and
#: reaching `CATALOG` the same way every volume does — imported and spread.
SHARD_DIR = "catalog"

#: An import with its clause: `import A from 'x'`, `import A, { b } from 'x'`.
IMPORT_CLAUSE = re.compile(
    r"""import\s+(type\s+)?([^'"]*?)\s+from\s+['"]([^'"]+)['"]""", re.DOTALL
)


def key_of(rel: str) -> str:
    """`frontend/src/x/Y.vue` -> `src/x/Y.vue`: the page lists paths frontend-relative."""
    if not rel.startswith("frontend/"):
        raise ValueError(f"not under frontend/: {rel}")
    return rel[len("frontend/") :]


def subject_paths(root: Path) -> list[str]:
    """Every `.vue` under `frontend/src` outside the preview site, repo-relative."""
    src = root / "frontend" / "src"
    if not src.is_dir():
        raise SystemExit(f"no frontend/src/ under {root}")
    out = []
    for path in sorted(src.rglob("*.vue")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(DEMO_DIR):
            out.append(rel)
    return out


def default_local(clause: str) -> str | None:
    """The default binding of an import clause: `A, { b }` -> `A`; `{ b }` -> None.

    `{ default as A }` is a default import too, spelled the long way.
    """
    clause = clause.strip()
    if clause.startswith("*"):
        return None
    head, _, brace = clause.partition("{")
    head = head.strip().rstrip(",").strip()
    if head:
        return head
    match = re.search(r"\bdefault\s+as\s+([A-Za-z_$][\w$]*)", brace)
    return match.group(1) if match else None


def strip_comments(text: str) -> str:
    """`text` with every `//` and `/* */` comment blanked, strings kept.

    A small scanner, not a regex: `'https://x'` holds a `//` that is not a
    comment, and a comment can hold a quote that opens no string. Template
    literals are followed through `${ ... }` (code again, braces counted), so
    a comment inside an interpolation is stripped and a backtick inside one
    does not end the outer literal. Regex literals are not recognised — the
    catalog has none, and the failure mode is keeping text, not losing it.
    Newlines inside a comment are kept so line structure survives.
    """
    out: list[str] = []
    i, n = 0, len(text)
    # Each frame is a template literal we are inside; its value is the brace
    # depth of the `${` we are currently in (0 = in the literal's text).
    templates: list[int] = []
    while i < n:
        ch = text[i]
        in_template_text = bool(templates) and templates[-1] == 0
        if in_template_text:
            if ch == "\\":
                out.append(text[i : i + 2])
                i += 2
            elif ch == "`":
                templates.pop()
                out.append(ch)
                i += 1
            elif text.startswith("${", i):
                templates[-1] = 1
                out.append("${")
                i += 2
            else:
                out.append(ch)
                i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            stop = n if end == -1 else end + 2
            out.append("".join(c for c in text[i:stop] if c == "\n"))
            out.append(" ")
            i = stop
            continue
        if ch in "'\"":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if ch == "`":
            templates.append(0)
            out.append(ch)
            i += 1
            continue
        if templates and ch == "{":
            templates[-1] += 1
        elif templates and ch == "}":
            templates[-1] -= 1  # back to 0: the interpolation closed
        out.append(ch)
        i += 1
    return "".join(out)


def named_locals(clause: str) -> list[str]:
    """The local names an import clause binds: `A, { b, c as d }` -> [A, b, d]."""
    clause = clause.strip()
    head, _, brace = clause.partition("{")
    names = []
    head = head.strip().rstrip(",").strip()
    if head and not head.startswith("*"):
        names.append(head)
    for part in brace.rstrip("} \n").split(","):
        part = re.sub(r"^\s*type\s+", "", part).strip()
        if part:
            names.append(part.split(" as ")[-1].strip())
    return names


def volume_target(demo: Path, importer: Path, spec: str) -> Path | None:
    """Where a relative import lands, when it lands on a catalog volume.

    Two shapes count, and nothing else does:

      * a flat `catalog*.ts` beside the entry point — the original volumes;
      * a `.ts` under `catalog/` — the scene shards.

    Everything else a relative `.ts` import of theirs might be is refused: the
    router, a scene module, a helper. That is on purpose — "which files are
    catalog volumes" is a question about the layout, and answering it by
    guessing from a name would let a `.ts` that the site never renders in.
    """
    if not spec.startswith("./"):
        return None
    name = spec[2:]
    if not name.endswith(".ts"):
        name += ".ts"
    if name.endswith("Fixtures.ts") or SPEC.search(name):
        return None
    target = Path(os.path.normpath(importer.parent / name))
    try:
        parts = target.relative_to(demo).parts
    except ValueError:
        return None  # outside the preview site
    if len(parts) == 1 and not VOLUME.match(parts[0]):
        return None
    if len(parts) > 2 or (len(parts) == 2 and parts[0] != SHARD_DIR):
        return None
    return target


def catalog_files(root: Path, sources: Any) -> list[Path]:
    """`catalog.ts`, every volume beside it and every shard under `catalog/`.

    Only what `catalog.ts` reaches is on the site: `DemoCatalog.vue` and
    `catalog.spec.ts` both read `CATALOG` from it, and a volume reaches
    `CATALOG` only by being imported and spread there. So a volume is followed
    only when one of the names it is imported as is spread (`...X_ENTRIES`) in
    the importing file; a `.ts` beside it that nobody imports (an orphan
    volume), or imports without spreading, the router, the scene modules and
    the fixture files (`catalog*Fixtures.ts` — data, never entries) are not
    walked. Which files are volumes at all is `volume_target`'s answer. Imports
    are read after `strip_comments`, so a commented-out import or spread
    follows nothing.

    `sources` is the run's text reader (`frontend_grade.SourceTexts`). Every
    file walked here is also one the grader's import graph read, so sharing the
    reader is what keeps a run at one read per path instead of two.
    """
    demo = root / DEMO_DIR
    entry = demo / "catalog.ts"
    if not entry.is_file():
        return []
    seen: list[Path] = []
    todo = [entry]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.append(path)
        try:
            text = strip_comments(sources.read(path))
        except OSError as exc:
            raise SystemExit(f"cannot read {path}: {exc}") from exc
        for type_only, clause, spec in IMPORT_CLAUSE.findall(text):
            if type_only:
                continue
            target = volume_target(demo, path, spec)
            if target is None:
                continue
            if not any(
                re.search(r"\.\.\.\s*" + re.escape(local) + r"(?![\w$])", text)
                for local in named_locals(clause)
            ):
                continue  # imported, but none of its arrays is spread into a list
            if target.is_file():
                todo.append(target)
    return sorted(seen)


def catalogued(
    root: Path, grade_module: Any, sources: Any = None
) -> dict[str, list[str]]:
    """`{component rel: [catalog files that render it]}`.

    A component is catalogued by a file `catalog_files` reaches from
    `catalog.ts` that value-imports it AND uses that binding as an entry's
    `component:` — both read with comments stripped.

    `sources` is the run's text reader. `catalog_files` has already read every
    file it returns, so reading each one again here is the duplicate this
    parameter exists to remove; without one, this makes a reader of its own.
    """
    sources = grade_module.SourceTexts() if sources is None else sources
    src = root / "frontend" / "src"
    found: dict[str, list[str]] = {}
    for path in catalog_files(root, sources):
        try:
            text = strip_comments(sources.read(path))
        except OSError as exc:
            raise SystemExit(f"cannot read {path}: {exc}") from exc
        here = path.relative_to(root).as_posix()
        for type_only, clause, spec in IMPORT_CLAUSE.findall(text):
            if type_only or not spec.endswith(".vue"):
                continue
            local = default_local(clause)
            if local is None:
                continue
            uses = re.compile(
                r"\bcomponent\s*:\s*(?:markRaw\s*\(\s*)?" + re.escape(local) + r"(?![\w$])"
            )
            if not uses.search(text):
                continue  # imported, but no entry renders it
            resolved = grade_module.resolve_spec(spec, path, src)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            found.setdefault(rel, []).append(here)
    return found
