"""One row per frontend component — grade, preview-site status, scene — as JSON.

Three questions about the same `frontend/src` tree already have one owner each,
and this page must agree with all three, so it loads them and asks:

  - the grade (A is standalone-ready): `.claude/scripts/frontend_grade.py`, the
    repo's only grader — the board and both ratchets call the same function;
  - whether `/demo/catalog` shows a component: `gen/catalog_reach.py`, which
    reads the preview site's own entry files;
  - which components are *scenes* (a page, a page's view, or a panel):
    `.claude/scripts/scene-ratchet.py`, whose definition is written down in
    `docs/manual/dev/scenes.md`.

Read, never re-decided: a second answer to the grade or the scene set would
drift from the check that gates on it. Same reason `gen/env.py` parses the
settings module instead of importing it.

`components` is every `.vue` under `frontend/src` except the preview site's own
(`views/demo/`), so the page can say which of those components are not on the
site.

Output on stdout: `{totals, components}`. Each component is
`{path, grade, catalogued, state, scene}` — `path` is frontend-relative (the
same form the preview site's imports resolve to), `state` is
`catalogued | standalone | uncatalogued` (standalone: grade A, so it could be
shown alone, but the site does not show it), and
`scene` is `page | view | panel | null`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".claude" / "scripts"
# The ratchets import their shared report helper by name, so their directory
# has to be importable before they are loaded.
sys.path.insert(0, str(SCRIPTS))


def load(name: str, filename: str, directory: Path = SCRIPTS):
    """A script as a module — those under `.claude/scripts` have __main__ guards."""
    path = directory / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


grade = load("_docs_frontend_grade", "frontend_grade.py")
scene = load("_docs_scene_ratchet", "scene-ratchet.py")
catalog = load("_docs_catalog_reach", "catalog_reach.py", Path(__file__).resolve().parent)

reach = grade.api_reach(ROOT)
grades = {
    rel: grade.grade_component(ROOT, ROOT / rel, reach)
    for rel in catalog.subject_paths(ROOT)
}
shown = set(catalog.catalogued(ROOT, grade))

scenes = set(scene.scene_paths(ROOT, reach))
S_PANELS = "frontend/src/components/panels/"
S_VIEWS = "frontend/src/views/"
# A page that hands its rendering to a sibling `<stem>View.vue` is a container;
# the view is the scene that is frozen, and `paired_views` is what decides it
# (the same function scene-ratchet walks with). Offering it every `views/`
# scene as a candidate page is safe: a `<stem>View.vue` has no
# `<stem>ViewView.vue` to pair with.
paired = scene.paired_views(ROOT, {s for s in scenes if s.startswith(S_VIEWS)}, reach)
views = set(paired.values())


def scene_kind(rel: str) -> str | None:
    """Which scene this component is — or none, if it is not a scene at all."""
    if rel not in scenes:
        return None
    if rel.startswith(S_PANELS):
        return "panel"
    return "view" if rel in views else "page"


def state_of(rel: str) -> str:
    """On the site, could be (grade A), or neither."""
    if rel in shown:
        return "catalogued"
    return "standalone" if grades[rel].standalone else "uncatalogued"


components = [
    {
        "path": catalog.key_of(rel),
        "grade": grades[rel].letter,
        "catalogued": rel in shown,
        "state": state_of(rel),
        "scene": scene_kind(rel),
    }
    for rel in sorted(grades)
]

states = Counter(c["state"] for c in components)
kinds = Counter(c["scene"] for c in components if c["scene"])
json.dump(
    {
        "totals": {
            "components": len(components),
            "scenes": len(scenes),
            "grades": {g: sum(1 for c in components if c["grade"] == g) for g in "ABCD"},
            "states": {s: states[s] for s in ("catalogued", "standalone", "uncatalogued")},
            "scene_kinds": {k: kinds[k] for k in ("page", "view", "panel")},
        },
        "components": components,
    },
    sys.stdout,
    ensure_ascii=False,
)
