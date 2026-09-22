"""The pinned Claude Code build, for tests whose executor runs commands.

Every command an executor runs goes through that build's `mcp serve`, so a
test that runs one needs the build itself; python standing in for it no longer
gets past the first `Bash`.
"""

import os
import shutil


def claude_binary():
    claude = os.environ.get("CHEESE_TEST_CLAUDE") or shutil.which("claude")
    if not claude:
        raise RuntimeError(
            "CHEESE_TEST_CLAUDE must point to the pinned Claude Code build"
        )
    return claude
