"""Prepare an isolated, disposable project on the execution host."""

import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
work = root / "remote project"
work.mkdir(parents=True)
(work / "target.txt").write_text("BEFORE_EDIT\n")
(work / "CLAUDE.md").write_text("Project marker: REMOTE_PROJECT_INSTRUCTIONS.\n")
skill = work / ".claude/skills/remote-check"
skill.mkdir(parents=True)
(skill / "SKILL.md").write_text(
    "---\nname: remote-check\ndescription: Check the remote execution acceptance marker.\nallowed-tools: Bash\n---\nREMOTE_SKILL_SENTINEL\nEnvironment: !`printf '%s' \"$EXECUTION_ENV\"`\n"
)
subprocess.run(["git", "init", "-q", str(work)], check=True)
subprocess.run(["git", "add", "."], cwd=work, check=True)
subprocess.run(
    [
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "seed",
    ],
    cwd=work,
    check=True,
)
print(json.dumps({"workspace": str(work)}))
