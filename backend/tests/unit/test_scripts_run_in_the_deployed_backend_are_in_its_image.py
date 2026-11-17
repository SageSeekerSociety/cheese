"""Every backend script a deploy workflow runs inside the backend container is
copied into the backend image.

The image copies `backend/scripts/` file by file (backend/Dockerfile), so a new
script the deploy runs with `docker exec … python -m scripts.<name>` is absent
from the container until its own COPY line is added. The deploy step that runs
it is continue-on-error, so the deploy stays green and the script silently
never runs: on 2026-11-17 `scripts.ship_tasks` failed with "No module named
scripts.ship_tasks" on every dev deploy after #5281, and no task ever waited
for its deploy.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "backend" / "Dockerfile"
WORKFLOWS = ROOT / ".github" / "workflows"

_RUN_IN_CONTAINER = re.compile(
    r"docker (?:compose )?exec\b[^\n]*?python -m scripts\.(\w+)"
)
_COPIED = re.compile(r"^COPY\b[^\n]*\bscripts/(\w+)\.py\s+\./scripts/", re.MULTILINE)


def _run_in_container() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for name in _RUN_IN_CONTAINER.findall(workflow.read_text()):
            found.setdefault(name, []).append(workflow.name)
    return found


def test_the_guard_sees_the_deploy_scripts():
    assert {"ship_feedback", "ship_tasks"} <= set(_run_in_container())


def test_every_script_a_deploy_runs_in_the_backend_is_copied_into_its_image():
    copied = set(_COPIED.findall(DOCKERFILE.read_text()))
    missing = {
        name: where for name, where in _run_in_container().items() if name not in copied
    }
    assert not missing, (
        "backend/Dockerfile does not COPY these scripts that a workflow runs "
        f"inside the backend container: {missing}"
    )
