"""Build the ratchet-board preview page from real data collected on the work machine.

Inputs (all real, collected 2026-09-30):
  ../out/series.json       50 arch-metrics artifacts from main pushes (CI)
  ../out/snap/*.log         checker runs on main@dc069a4
  ../out/snap/arch-metrics.json
"""
import json, pathlib, html

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path("/home/cheese/.cheese/home/de808b13-ffd2-4b8a-9d1d-fba7babe389f/14047829-f6b3-410d-bdd8-1846943c9113/work/out")
SNAP_SHA = "ccc8a18e4"
series = json.loads((OUT / "series.json").read_text())
# The page reads one snapshot; history stops at that commit so every number shares one SHA.
series = series[: [r["sha"] for r in series].index(SNAP_SHA) + 1]
snap = json.loads((OUT / "snap3" / "arch-metrics.json").read_text())

tpl = (HERE / "template.html").read_text()
data = {
    "series": [
        {k: r[k] for k in ("date", "sha", "subject", "A", "comp", "fe_boundary", "c1", "c2", "c3",
                           "over", "excess", "fe_over", "be_over", "chat", "deferred")}
        for r in series
    ],
    "worst_now": snap["size"]["worst"],
    "worst_first": series[0]["worst"],
}
(HERE / "index.html").write_text(tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False)))
print("ok", len(data["series"]))
