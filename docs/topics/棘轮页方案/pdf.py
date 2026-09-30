"""Generate the Chinese review PDF (typst) for the ratchet board proposal."""
import json, pathlib, subprocess

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path(__import__("os").environ["HOME"]) / "work" / "out"
S = json.loads((OUT / "series.json").read_text())


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
        i = next(i for i, r in enumerate(S) if r["sha"] == mark)
        x = pad + i * (w - 2 * pad) / (len(vals) - 1)
        m = f'#place(line(start: ({x:.1f}pt, 0pt), end: ({x:.1f}pt, {h}pt), stroke: (paint: rgb("#2F5AA8"), dash: "dashed", thickness: 0.6pt)))'
    segs = "".join(f'#place(line(start: {a}, end: {b}, stroke: 1pt + rgb("#5A5E66")))' for a, b in zip(pts, pts[1:]))
    return f'#box(width: {w}pt, height: {h}pt)[{m}{segs}]'


doc = (HERE / "review.typ.tpl").read_text()
for key, mark in [("fe_boundary", "14565ff91"), ("c3", None), ("c1", None), ("over", None), ("excess", None), ("chat", None)]:
    doc = doc.replace(f"%%TREND_{key}%%", trend(key, mark=mark))
(HERE / "review.typ").write_text(doc)
subprocess.run(["typst", "compile", str(HERE / "review.typ"), str(HERE / "棘轮页方案.pdf")], check=True)
subprocess.run(["typst", "compile", "--format", "png", "--ppi", "70", str(HERE / "review.typ"), str(HERE / "page-{p}.png")], check=True)
print("ok")
