"""How long each test module and test takes in each backend layer, read back from CI.

``scripts/ci_shard.py`` hands a shard's modules to xdist longest first, so the
last minutes of a run are spent on short modules and no worker sits idle while
one long module finishes alone. This writes the timings it orders by.

    uv run python -m scripts.test_durations <dir> [<dir> ...]

Each directory is one CI run's downloaded backend artifacts
(``gh run download <run> -p 'backend-*' -D <dir>``), holding
``backend-<layer>-*/results.xml``. Several runs are combined by taking each
module's and test's median, because hosted runners differ in speed from run to
run. Layer maps retain module seconds and case counts for scheduling; the
additional ``tests`` map records seconds by full nodeid for slow-test queries.
"""

import json
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

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


def _cases(directory: Path):
    """Yield ``(layer, node id, seconds)``, one per test the run reported."""
    for report in directory.rglob("results.xml"):
        layer = _layer(report)
        if layer is None:
            continue
        for case in ET.parse(report).iter("testcase"):
            nodeid = _nodeid(case)
            if nodeid:
                yield layer, nodeid, float(case.get("time", 0))


class Run(NamedTuple):
    """Module totals and full-nodeid totals from a single pass over one run."""

    modules: dict[str, dict[str, tuple[float, int]]]
    tests: dict[str, dict[str, float]]


def read_run(directory: Path) -> Run:
    """Sum every reported occurrence, retaining module counts for scheduling."""
    by_module: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    by_test: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for layer, nodeid, seconds in _cases(directory):
        by_module[layer][nodeid.split("::")[0]].append(seconds)
        by_test[layer][nodeid].append(seconds)
    return Run(
        modules={
            layer: {
                module: (sum(times), len(times)) for module, times in modules.items()
            }
            for layer, modules in by_module.items()
        },
        tests={
            layer: {nodeid: sum(times) for nodeid, times in tests.items()}
            for layer, tests in by_test.items()
        },
    )


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


def combine_tests(runs: list[Run]) -> dict[str, float]:
    """Median per observed run; absent tests are not zero-second samples."""
    samples: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        per_run: dict[str, float] = defaultdict(float)
        for cases in run.tests.values():
            for nodeid, seconds in cases.items():
                per_run[nodeid] += seconds
        for nodeid, seconds in per_run.items():
            samples[nodeid].append(seconds)
    return {
        nodeid: round(statistics.median(seconds), 2)
        for nodeid, seconds in sorted(samples.items())
    }


def render(combined: dict) -> str:
    """JSON with one module or test per line for short refresh diffs."""
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
    if not any(run.modules for run in runs):
        print("no backend-<layer>-*/results.xml with a cheese_nodeid", file=sys.stderr)
        return 1
    combined = combine([run.modules for run in runs])
    combined["tests"] = combine_tests(runs)
    OUTPUT.write_text(render(combined))
    counts = {layer: len(combined[layer]) for layer in LAYERS if layer in combined}
    print(
        f"wrote {OUTPUT.name}: {counts} modules, "
        f"{len(combined['tests'])} tests from {len(runs)} run(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
