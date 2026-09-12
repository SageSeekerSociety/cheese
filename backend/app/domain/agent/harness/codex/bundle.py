"""Build the standard-library-only runner archive sent to the session host."""

import io
import zipfile
from pathlib import Path


def build() -> bytes:
    source = Path(__file__).resolve().parents[4]
    files = {
        "__main__.py": (
            "from app.domain.agent.harness.codex.entry import main\nmain()\n"
        ),
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/agent/__init__.py": "",
        "app/domain/agent/harness/codex/__init__.py": "",
    }
    for relative in (
        "domain/agent/service.py",
        "domain/agent/executor_transport.py",
        "domain/agent/harness/__init__.py",
        "domain/agent/harness/codex/app_server.py",
        "domain/agent/harness/codex/session.py",
        "domain/agent/harness/codex/journal.py",
        "domain/agent/harness/codex/runner.py",
        "domain/agent/harness/codex/tools.py",
        "domain/agent/harness/codex/entry.py",
    ):
        files[f"app/{relative}"] = (source / relative).read_text()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in sorted(files.items()):
            # Fixed metadata makes the artifact digest depend only on its sources.
            archive.writestr(zipfile.ZipInfo(name), content)
    return output.getvalue()
