"""The wolfram skill runs inside somebody else's checkout and keeps its own settings.

The script is started with the room's project as its working directory, on a
machine whose environment belongs to its owner. A `.env` in that project, or a
variable in that environment, named like one of the skill's settings must not
point it at another server or switch its safety gate off.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.domain.agent.skills import _NATIVE_SKILL_SRC

SCRIPT = _NATIVE_SKILL_SRC / "wolfram" / "scripts" / "wolfram_mcp.py"
ELSEWHERE = "http://127.0.0.1:9/mcp"


def _execute(code: str, project: Path) -> dict:
    (project / ".env").write_text(f"MCP_URL={ELSEWHERE}\nSAFETY_ENFORCE=false\n")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(project),
        "MCP_URL": ELSEWHERE,
        "SAFETY_ENFORCE": "false",
        # Run from the source tree: a __pycache__ there fails test_native_skill_files.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "call",
            "wolfram_execute",
            json.dumps({"code": code}),
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    head, _, _ = result.stdout.partition("\n}\n")
    return json.loads(head + "\n}")


def test_a_forbidden_expression_is_refused_whatever_the_project_configures(tmp_path):
    outcome = _execute('Import["/etc/hosts"]', tmp_path)

    assert outcome["ok"] is False
    assert outcome["strategy"] == "blocked"
    assert [v["rule"] for v in outcome["violations"]] == ["symbol:Import"]
