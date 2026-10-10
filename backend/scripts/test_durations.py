"""How long each test module takes in each backend layer, read back from CI.

``scripts/ci_shard.py`` hands a shard's modules to xdist longest first, so the
last minutes of a run are spent on short modules and no worker sits idle while
one long module finishes alone. This writes the timings it orders by.

    uv run python -m scripts.test_durations <dir> [<dir> ...]

Each directory is one CI run's downloaded backend artifacts
(``gh run download <run> -p 'backend-*' -D <dir>``), holding
``backend-<layer>-*/results.xml``. Several runs are combined by taking each
module's median, because hosted runners differ in speed from run to run.
"""

import json
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "module_durations.json"
LAYERS = ("pure", "contract", "integration")


def _layer(report: Path) -> str | None:
    for part in report.parts:
        for layer in LAYERS:
            if part.startswith(f"backend-{layer}-"):
                return layer
    return None


def _nodeid(case: ET.Element) -> str | None:
    for prop in case.iter("property"):
        if prop.get("name") == "cheese_nodeid":
            return prop.get("value")
    return None


def read_run(directory: Path) -> dict[str, dict[str, tuple[float, int]]]:
    """``{layer: {module: (seconds, tests)}}`` for one run."""
    totals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for report in directory.rglob("results.xml"):
        layer = _layer(report)
        if layer is None:
            continue
        for case in ET.parse(report).iter("testcase"):
            nodeid = _nodeid(case)
            if nodeid:
                totals[layer][nodeid.split("::")[0]].append(float(case.get("time", 0)))
    return {
        layer: {module: (sum(times), len(times)) for module, times in modules.items()}
        for layer, modules in totals.items()
    }


def combine(runs: list[dict[str, dict[str, tuple[float, int]]]]) -> dict:
    out: dict[str, dict[str, list[float | int]]] = {}
    for layer in LAYERS:
        seen: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for run in runs:
            for module, value in run.get(layer, {}).items():
                seen[module].append(value)
        if seen:
            out[layer] = {
                module: [
                    round(statistics.median(s for s, _ in values), 2),
                    max(n for _, n in values),
                ]
                for module, values in sorted(seen.items())
            }
    return out


def render(combined: dict) -> str:
    """JSON with one module per line, so a refresh reads as a short diff."""
    blocks = []
    for layer, modules in combined.items():
        rows = ",\n".join(
            f"    {json.dumps(m)}: {json.dumps(v)}" for m, v in modules.items()
        )
        blocks.append(f"  {json.dumps(layer)}: {{\n{rows}\n  }}")
    return "{\n" + ",\n".join(blocks) + "\n}\n"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    runs = [read_run(Path(d)) for d in argv]
    if not any(runs):
        print("no backend-<layer>-*/results.xml with a cheese_nodeid", file=sys.stderr)
        return 1
    combined = combine(runs)
    OUTPUT.write_text(render(combined))
    counts = {layer: len(modules) for layer, modules in combined.items()}
    print(f"wrote {OUTPUT.name}: {counts} modules from {len(runs)} run(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
