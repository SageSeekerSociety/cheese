"""Build the standard-library-only runner archive the session machine runs.

A zip rather than loose files because the modules import each other by package
path, and rewriting those imports for the machine is a second version of the
same code to be wrong in. The archive's digest depends only on its sources, so
a launch that ships an unchanged runner writes nothing.
"""

import io
import zipfile
from pathlib import Path


def build() -> bytes:
    source = Path(__file__).resolve().parents[4]
    files = {
        "__main__.py": "from app.domain.agent.harness.pi.entry import main\nmain()\n",
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/agent/__init__.py": "",
        "app/domain/agent/harness/pi/__init__.py": "",
    }
    for relative in (
        "domain/agent/service.py",
        # The platform CLI's own argparse tree, whole: it is one
        # standard-library-only file, and a second copy of the mapping between
        # a command and a tool schema is the thing worth carrying it to avoid.
        "domain/agent/cli_worker.py",
        "domain/agent/harness/__init__.py",
        "domain/agent/harness/pi/rpc.py",
        "domain/agent/harness/pi/journal.py",
        "domain/agent/harness/pi/catalog.py",
        "domain/agent/harness/pi/runner.py",
        "domain/agent/harness/pi/entry.py",
    ):
        files[f"app/{relative}"] = (source / relative).read_text()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in sorted(files.items()):
            # Fixed metadata makes the artifact digest depend only on its sources.
            archive.writestr(zipfile.ZipInfo(name), content)
    return output.getvalue()
