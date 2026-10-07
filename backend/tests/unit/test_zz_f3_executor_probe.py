"""TEMPORARY negative control for the /proc executor scan (draft PR, never merged).

Leaves a process whose command line looks like an executor under tmp_path;
teardown must fail this test by name and kill it.
"""

import subprocess
import sys


def test_leaves_an_executor_running(tmp_path):
    fake = tmp_path / "remote-execution" / "runtime.py"
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", str(fake), "serve"],
        start_new_session=True,
    )
