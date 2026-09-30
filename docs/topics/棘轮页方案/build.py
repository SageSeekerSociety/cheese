"""Build the ratchet-board preview page from real data collected on the work machine.

Inputs (all real, collected 2026-09-30):
  ../out/series.json       50 arch-metrics artifacts from main pushes (CI)
  ../out/snap/*.log         checker runs on main@dc069a4
  ../out/snap/arch-metrics.json
"""
import json, pathlib, html

HERE = pathlib.Path(__file__).parent
OUT = HERE  # series.json sits next to this file
series = json.loads((OUT / "series.json").read_text())
snap = json.loads((OUT / "snap" / "arch-metrics.json").read_text())

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
