"""The pinned Claude Code build, for tests whose executor runs it.

An executor serves its file tools through that build's `mcp serve`, and its
commands start from the shell snapshot that build writes, so a test that runs
either needs the build itself.
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
