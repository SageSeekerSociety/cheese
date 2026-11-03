"""The frontend container is told the deployment's other origins.

`frontend/scripts/mainland-redirect.sh` writes the redirect from `FRONTEND_URL`
and `FRONTEND_ALIASES`, and nothing else gives it either: the frontend service
declares no `env_file` (deliberately — it used to load the backend's secrets),
so every variable it can see is one compose lists under its `environment`.
`FRONTEND_URL` was listed and `FRONTEND_ALIASES` was not, which the redirect
cannot tell apart from a deployment with no aliases: a visitor arriving on one
of them (okcheese.com and cheese.ruc.edu.cn are aliases of the same deployment)
was never sent to the mainland entry. The app's own config is the other half of
the same list, and the two are read the same way, so this checks they are
declared together.
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
COMPOSE = BACKEND.parent / "deploy/compose/docker-compose.base.yml"


def _frontend_environment() -> list[str]:
    """The `environment:` entries of the `frontend:` service, one per line."""
    text = COMPOSE.read_text()
    start = re.search(r"^  frontend:\n", text, re.MULTILINE)
    assert start is not None, "docker-compose.base.yml has no frontend service"
    rest = text[start.end() :]
    # The service ends at the next line at the service level (two spaces).
    end = re.search(r"^  \w[\w-]*:\n", rest, re.MULTILINE)
    section = rest[: end.start()] if end else rest
    entries, inside = [], False
    for line in section.splitlines():
        if line.strip() == "environment:":
            inside = True
            continue
        if inside:
            # Comments sit inside the block here, at the same indentation.
            if line.lstrip().startswith("#"):
                continue
            # The block ends at the next key at the same indentation.
            if not line.startswith("      - "):
                break
            entries.append(line.strip()[2:])
    assert entries, "the frontend service declares no environment"
    return entries


def test_the_redirect_reads_the_origins_the_container_is_given():
    declared = {entry.split("=", 1)[0] for entry in _frontend_environment()}
    # Every variable mainland-redirect.sh reads, besides the entry itself.
    # Losing any one of them is a deployment that silently redirects nobody, or
    # the wrong nobody: the script treats an unset list as "no aliases".
    assert {"FRONTEND_URL", "FRONTEND_ALIASES", "MAINLAND_ENTRY"} <= declared, (
        f"frontend environment declares {sorted(declared)}"
    )
