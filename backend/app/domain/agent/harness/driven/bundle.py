"""Build the standard-library-only runner archive a session machine runs.

A zip rather than loose files because the modules import each other by package
path, and rewriting those imports for the machine is a second version of the
same code to be wrong in. The archive's digest depends only on its sources, so
a launch that ships an unchanged runner writes nothing.
"""

import io
import zipfile
from pathlib import Path

#: What every runner imports, whatever it drives.
SHARED = (
    "domain/agent/service.py",
    "domain/delivery/input_identity.py",
    "domain/agent/harness/__init__.py",
    "domain/agent/harness/driven/journal.py",
    "domain/agent/harness/driven/runner.py",
)

#: The Windows half of what a machine's programs ask of the operating system.
PORTABLE = "domain/agent/harness/claude_code/remote_execution/portable.py"


def build(
    entry: str, modules: tuple[str, ...], extra: dict[str, str] | None = None
) -> bytes:
    """``entry`` is the dotted module whose ``main`` the archive runs,
    ``modules`` the harness's own files as paths under ``app/``, and ``extra``
    any archive entry, by name, whose source is not a module under ``app/``."""
    source = Path(__file__).resolve().parents[4]
    package = entry.rpartition(".")[0].replace(".", "/")
    files = {
        "__main__.py": f"from {entry} import main\nmain()\n",
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/agent/__init__.py": "",
        "app/domain/delivery/__init__.py": "",
        "app/domain/agent/harness/driven/__init__.py": "",
        f"{package}/__init__.py": "",
    }
    for relative in SHARED + modules:
        files[f"app/{relative}"] = (source / relative).read_text()
    # What stands in for flock and process groups on Windows, imported by the
    # runner there under its own name: the same file the executor runs, not a
    # second version of it.
    files["portable.py"] = (source / PORTABLE).read_text()
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in sorted(files.items()):
            # Fixed metadata makes the artifact digest depend only on its sources.
            archive.writestr(zipfile.ZipInfo(name), content)
    return output.getvalue()
