"""Generate committed sandbox assets from their single Python source so the
Docker image and the runtime launcher can't drift (fusion-design §8.6).

Currently emits ``backend/sandbox/cheese-hook`` — the platform's event forwarder
baked into the tmux image via ``COPY``. Its source of truth is
``app.domain.agent.hook_forwarder.CHEESE_HOOK_SCRIPT``, also written by the
device launcher at runtime. Run this after editing that constant:

    uv run python scripts/gen-sandbox-assets.py

``tests/unit/test_hooks_substrate.py`` asserts the committed file matches the
constant, so any drift fails the suite.
"""

import sys
from pathlib import Path

# Make `app` importable when run as a plain script (scripts/ sits next to app/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.agent.hook_forwarder import CHEESE_HOOK_SCRIPT  # noqa: E402

# The committed forwarder file the tmux image COPYs (build context = backend/sandbox).
CHEESE_HOOK_FILE = Path(__file__).resolve().parents[1] / "sandbox" / "cheese-hook"


def main() -> None:
    CHEESE_HOOK_FILE.write_text(CHEESE_HOOK_SCRIPT, encoding="utf-8")
    print(f"wrote {CHEESE_HOOK_FILE}")


if __name__ == "__main__":
    main()
