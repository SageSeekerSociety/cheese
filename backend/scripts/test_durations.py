"""How long each test module and test takes in each backend layer, read back from CI.

``scripts/ci_shard.py`` hands a shard's modules to xdist longest first, so the
last minutes of a run are spent on short modules and no worker sits idle while
one long module finishes alone. This writes the timings it orders by.

    uv run python -m scripts.test_durations <dir> [<dir> ...]

Each directory is one CI run's downloaded backend artifacts
(``gh run download <run> -p 'backend-*' -D <dir>``), holding
``backend-<layer>-*/results.xml``. Several runs are combined by taking each
module's median, because hosted runners differ in speed from run to run.

The same runs also answer the question ``tests/slow_tests.txt`` asks: how slow
the tests it lists are now. It prints that as a table, worst first, which is
the order the file keeps its lines in — so a refresh is reading the table,
checking which of those tests a fix has already reached, and writing the rest
back in the order printed.
"""

import ast
import json
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

BACKEND = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND / "tests" / "module_durations.json"
SLOW_LIST = BACKEND / "tests" / "slow_tests.txt"
CONFTEST = BACKEND / "tests" / "conftest.py"
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
    """One CI run, cut both ways: by module, and by test.

    ``modules`` is what ``ci_shard.py`` orders by; ``tests`` is what the slow
    list is about. Both come off one pass over the reports — a run's artifacts
    are ~150MB of XML, and parsing them twice costs a minute.
    """

    #: ``{layer: {module: (seconds, tests)}}``.
    modules: dict[str, dict[str, tuple[float, int]]]
    #: ``{layer: {node id: seconds}}``.
    tests: dict[str, dict[str, float]]


def read_run(directory: Path) -> Run:
    """Both tables for one run.

    A test reported twice in one run — a rerun — counts once in ``tests``: the
    slow list asks how long a test takes, and a rerun is a second attempt at
    it, not more work. ``modules`` counts every report, so a flaky module's
    reruns stay visible in the estimate.
    """
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


def render(combined: dict) -> str:
    """JSON with one module per line, so a refresh reads as a short diff."""
    blocks = []
    for layer, modules in combined.items():
        rows = ",\n".join(
            f"    {json.dumps(m)}: {json.dumps(v)}" for m, v in modules.items()
        )
        blocks.append(f"  {json.dumps(layer)}: {{\n{rows}\n  }}")
    return "{\n" + ",\n".join(blocks) + "\n}\n"


def listed_slow_tests() -> list[str]:
    """The node ids ``tests/slow_tests.txt`` lists, in the order it lists them."""
    lines = SLOW_LIST.read_text().splitlines()
    return [line.split()[0] for line in lines if line.strip() and line[0] != "#"]


def _ceilings() -> dict[str, float]:
    """Each layer's ceiling in seconds, as ``tests/conftest.py`` defines it.

    Read out of the source rather than imported: conftest pulls in the app, a
    database engine and pytest itself, and this script otherwise needs nothing
    but the standard library. The guard over the slow list fails if the two
    ever disagree, so the copy that drifts is the one that turns up red.
    """
    found = re.search(
        r"^_LAYER_CEILING_S = (\{[^}]*\})", CONFTEST.read_text(), re.MULTILINE
    )
    return ast.literal_eval(found.group(1)) if found else {}


def slow_report(runs: list[Run]) -> str:
    """Where the tests in ``tests/slow_tests.txt`` stand now, worst first.

    Each line the list can be written from: a test that some run put over the
    ceiling still belongs there, one that stayed under the list's own line in
    every run is where a fix has already landed.
    """
    listed = listed_slow_tests()
    seen: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for run in runs:
        for layer, tests in run.tests.items():
            for nodeid in listed:
                if nodeid in tests:
                    seen[nodeid].append((layer, tests[nodeid]))
    ceilings = _ceilings()
    rows = []
    for nodeid, points in seen.items():
        layer = max(
            {layer for layer, _ in points},
            key=lambda layer: sum(1 for seen_layer, _ in points if seen_layer == layer),
        )
        rows.append((max(seconds for _, seconds in points), layer, nodeid, len(points)))
    rows.sort(key=lambda row: -row[0])

    out = [f"tests/slow_tests.txt as these {len(runs)} run(s) measured it:"]
    under = 0
    for slowest, layer, nodeid, count in rows:
        ceiling = ceilings.get(layer)
        share = f"{slowest / ceiling:.0%} of the {layer} ceiling" if ceiling else ""
        flag = ""
        if ceiling and slowest < 0.6 * ceiling:
            flag = (
                f"  <- under the 60% line ({0.6 * ceiling:.1f}s), a fix may have landed"
            )
            under += 1
        out.append(f"  {slowest:5.1f}s  {layer:<11} {share:<28} {nodeid}{flag}")
        if count < len(runs):
            out.append(f"         (measured in {count} of {len(runs)} runs)")
    for nodeid in listed:
        if nodeid not in seen:
            out.append(f"  not seen in any of these runs: {nodeid}")
    if under:
        out.append(
            f"  {under} of {len(rows)} stayed under the line in every run: check each"
            " one, and drop its line in the change that made it fast."
        )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    runs = [read_run(Path(d)) for d in argv]
    if not any(run.modules for run in runs):
        print("no backend-<layer>-*/results.xml with a cheese_nodeid", file=sys.stderr)
        return 1
    combined = combine([run.modules for run in runs])
    OUTPUT.write_text(render(combined))
    counts = {layer: len(modules) for layer, modules in combined.items()}
    print(f"wrote {OUTPUT.name}: {counts} modules from {len(runs)} run(s)")
    print(slow_report(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
