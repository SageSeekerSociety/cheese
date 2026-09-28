"""Answer, with the gate's own code, what a set of changed paths selects.

The CI page shows which suites a merge diff runs, and it computes that in the
browser (src/ci-scope.mjs). Two implementations of one rule drift, so build.mjs
sends a list of sample path lists in on stdin and compares what comes back out
of `.github/scripts/required-ci.py`'s real `select()` with what the page
computed. A difference fails the build.

Nothing is imported from the backend: this is the CI script itself, by path.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / ".github" / "scripts" / "required-ci.py"
spec = importlib.util.spec_from_file_location("cheese_required_ci", PATH)
required_ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = required_ci
spec.loader.exec_module(required_ci)

samples = json.load(sys.stdin)
json.dump([required_ci.select(paths) for paths in samples], sys.stdout)
