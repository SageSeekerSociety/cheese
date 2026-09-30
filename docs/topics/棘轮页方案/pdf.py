"""Generate the Chinese review PDF (typst) for the ratchet board proposal.

Every number in `review.typ.tpl` is a `%%V:<path>%%` placeholder filled from
`snapshot.json` — the same file the preview page is built from — so the PDF and
the page cannot disagree. Trends (`%%TREND_<key>%%`) come from `series.json`,
the arch-metrics artifacts CI has stored on main pushes.

    python3 pdf.py
"""
import json
import os
import pathlib
import subprocess

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path(os.environ["HOME"]) / "work" / "out"
SERIES = HERE / "series.json" if (HERE / "series.json").exists() else OUT / "series.json"
S = json.loads(SERIES.read_text())
SNAP = json.loads((HERE / "snapshot.json").read_text())
CHECKS = {c["id"]: c for c in SNAP["checks"]}

#: A real run at the same commit as the snapshot:
#:   cd frontend && pnpm exec vitest run src/views/demo/catalog.spec.ts
#:   128 passed / 0 failed, 2026-09-30 09:03 UTC
MOUNT = {"components": 37, "cases": 125, "tests": 128}


def _num(v):
    return f"{v:,}" if isinstance(v, int) else str(v)


def _row(record, contract):
    """The record's `details` row for one backend contract, by contract id."""
    for row in record.get("details") or []:
        if row.get("file") == contract:
            return row
    return {}


def value(path):
    """Resolve one `%%V:<path>%%` against the snapshot."""
    if path in ("commit",):
        return SNAP["commit"][:9]
    if path in ("commit_date", "collected_at"):
        return (SNAP[path] or "").replace("T", " ")[:16]
    if path == "history":
        return _num(len(S))
    if path == "check-count":
        return _num(len(SNAP["checks"]))
    if path == "with-fingerprint":
        return _num(sum(1 for c in SNAP["checks"] if c.get("rule_fingerprint")))
    if path.startswith("mount-"):
        return _num(MOUNT[path[len("mount-"):]])
    if path.startswith("board.size."):
        return _num(SNAP["board"]["size"][path[len("board.size."):]])

    kind, rest = path.split(".", 1)
    parts = rest.split(".")
    if kind == "stale":
        # stale.<check>            -> how many exemptions that check outgrew
        # stale.be-contracts.<id>  -> how many that one contract outgrew
        record = CHECKS[parts[0]]
        if len(parts) == 1:
            return _num(len(record.get("stale") or []))
        return _num(_row(record, parts[1]).get("stale"))
    if kind != "check":
        raise KeyError(path)

    record = CHECKS[parts[0]]
    if len(parts) == 2:
        target = record
    elif len(parts) == 3:  # check.<checker>.<contract>.<field>
        target = _row(record, parts[1])
    elif len(parts) == 4 and parts[1] == "details":  # check.<checker>.details.<contract>.<field>
        target = _row(record, parts[2])
    else:
        raise KeyError(path)
    return _num(target.get(parts[-1]))


def trend(key, w=150, h=36, mark=None):
    vals = [r[key] for r in S]
    lo, hi = min(vals), max(vals)
    pad = 3
    pts = []
    for i, v in enumerate(vals):
        x = pad + i * (w - 2 * pad) / (len(vals) - 1)
        y = h / 2 if hi == lo else h - pad - (v - lo) * (h - 2 * pad) / (hi - lo)
        pts.append(f"({x:.1f}pt, {y:.1f}pt)")
    m = ""
    if mark:
        i = next(i for i, r in enumerate(S) if r["sha"].startswith(mark))
        x = pad + i * (w - 2 * pad) / (len(vals) - 1)
        m = f'#place(line(start: ({x:.1f}pt, 0pt), end: ({x:.1f}pt, {h}pt), stroke: (paint: rgb("#2F5AA8"), dash: "dashed", thickness: 0.6pt)))'
    segs = "".join(f'#place(line(start: {a}, end: {b}, stroke: 1pt + rgb("#5A5E66")))' for a, b in zip(pts, pts[1:]))
    return f'#box(width: {w}pt, height: {h}pt)[{m}{segs}]'


doc = (HERE / "review.typ.tpl").read_text()
for key, mark in [("fe_boundary", "14565ff91"), ("c3", None), ("c1", None), ("over", None), ("excess", None), ("chat", None)]:
    doc = doc.replace(f"%%TREND_{key}%%", trend(key, mark=mark))

# Fill every value placeholder, and refuse to compile with one left over.
seen = set()
while "%%V:" in doc:
    start = doc.index("%%V:")
    end = doc.index("%%", start + 4)
    token = doc[start + 4 : end]
    seen.add(token)
    doc = doc[:start] + value(token) + doc[end + 2 :]
left = [t for t in doc.split("%%") if t.startswith("TREND_")]
if left:
    raise SystemExit(f"unfilled trend placeholders: {left}")

(HERE / "review.typ").write_text(doc)
subprocess.run(["typst", "compile", str(HERE / "review.typ"), str(HERE / "棘轮页方案.pdf")], check=True)
subprocess.run(["typst", "compile", "--format", "png", "--ppi", "70", str(HERE / "review.typ"), str(HERE / "page-{p}.png")], check=True)
print(f"ok {len(seen)} placeholders, {len(S)} history rows, snapshot {SNAP['commit'][:9]}")
