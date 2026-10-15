"""Treat every Gemini after 2.x as the new generation, not only gemini-3*.

As pinned, LiteLLM decides "Gemini 3 or newer" by `"gemini-3" in model`, so
gemini-4-argon is handled as an old Gemini: an assistant tool call without a
thought signature (history from another model, or after compaction) is sent
unsigned and Gemini refuses the whole request with 400, tool-call ids are not
forwarded, and effort maps to the old thinking budget. Upstream replaced the
substring test with "anything that is not gemini-1.x/2.x/pro/flash/exp"; this
is that rule. Drop the patch when the base image carries it.
"""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "c5128a12b26fb701bf25d4c900358cb9ce2bb40d4e1db9149c15b2ae5f1562a8"
), "Review the upstream Gemini generation check before updating this patch"

old = (
    b'        # Check for Gemini 3 models\n'
    b'        if "gemini-3" in model:\n'
    b"            return True\n"
    b"        return False\n"
)
new = (
    b"        import re\n\n"
    b'        model_name = model.split("/")[-1].lower()\n'
    b"        if not model_name.startswith(\"gemini-\"):\n"
    b"            return False\n"
    b"        return not re.match(\n"
    b'            r"^gemini-(?:[12](?:\\.\\d+)?|exp|(?:pro|flash)(?!-(?:lite-)?latest$))(?:-|$)",\n'
    b"            model_name,\n"
    b"        )\n"
)
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
