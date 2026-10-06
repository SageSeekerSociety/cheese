"""The server process maps every table a foreign key points at.

The server never imports `app.models`; a model is mapped when some module the
server imports imports it. A foreign key to a table whose model nothing on that
path imports is fine in tests (which import `app.models`) and fails in
production the first time a row is written. Checked in a fresh interpreter,
importing exactly what the server imports.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

_PROBE = """
import app.main  # noqa: F401
from sqlalchemy.orm import configure_mappers
from app.core.db import Base

configure_mappers()
missing = sorted(
    f"{table.name}.{fk.parent.name} -> {fk.target_fullname}"
    for table in Base.metadata.tables.values()
    for fk in table.foreign_keys
    if fk.target_fullname.split(".")[0] not in Base.metadata.tables
)
print("\\n".join(missing))
"""


def test_every_foreign_key_target_is_mapped_in_the_server_process():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == ""
