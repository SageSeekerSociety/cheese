"""The backend and the sandbox ship ONE jj, because they share one jj store.

`ws.sandbox_vcs_mounts` bind-mounts a project's `.jj`/`.git` into every sandbox
container, so the backend process and the in-container agent are two jj binaries
operating the same files. jj is pre-1.0 and its on-disk store still moves, so
"whatever each image happened to pin" is a store two versions take turns
rewriting — and the side that loses is not the side that gets rebuilt.

The number itself lives in `backend/sandbox/Dockerfile`; CI installs the binary
it tests by reading that same ARG, so this test is the remaining pair.
"""

import re
from pathlib import Path

BACKEND_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"
SANDBOX_DOCKERFILE = Path(__file__).resolve().parents[2] / "sandbox" / "Dockerfile"


def _jj_pin(dockerfile: Path) -> str:
    match = re.search(r"^ARG JJ_VERSION=(\S+)$", dockerfile.read_text(), re.MULTILINE)
    assert match, f"{dockerfile} stopped pinning jj with an ARG JJ_VERSION line"
    return match.group(1)


def test_both_images_pin_the_same_jj():
    assert _jj_pin(BACKEND_DOCKERFILE) == _jj_pin(SANDBOX_DOCKERFILE)
