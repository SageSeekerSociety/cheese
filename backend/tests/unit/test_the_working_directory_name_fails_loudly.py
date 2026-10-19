"""The line's short name fails loudly when it was never set.

`$T` is bound in each command's own shell by the prefix (`shell_prefix`), and a
command that names it as `cd "$T"` in a shell where it is missing runs
`cd ""` — success, and the directory does not move. The commands after it then
work somewhere the session never chose. The prompt teaches the form that stops
instead.
"""

import os
import subprocess

from app.domain.agent.harness.prompt import WORKING_DIRECTORY as TOLD

CD = 'cd "${T:?T is not set}" && pwd -P'


def _run(command, cwd, **overrides):
    environment = {key: value for key, value in os.environ.items() if key != "T"}
    environment.update(overrides)
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
    )


def test_the_line_is_told_to_fail_when_the_name_is_missing():
    assert CD.split(" &&")[0] in TOLD
    assert 'cd "$T"' not in TOLD


def test_cd_without_the_name_stops_instead_of_staying_put(tmp_path):
    missing = _run(CD, tmp_path)
    assert missing.returncode != 0, "`cd` with an unset $T must not succeed"
    kept = _run(CD, tmp_path, T=str(tmp_path))
    assert kept.returncode == 0
    assert os.path.realpath(kept.stdout.strip()) == os.path.realpath(str(tmp_path))
