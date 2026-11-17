"""The launcher's five shipped programs are read from disk once, not per launch.

`sandbox/cheese` is 376 KiB and `launch_script` reads it (and four modules) on
every call — the call that starts a screen. Production's stall board has two
130-140 ms stalls whose whole stack is `read_text` under
`machine_launcher.screen_launch`, one of them in `open` and one in `decode`.
"""

import pytest

from app.domain.agent import machine_launcher


@pytest.fixture(autouse=True)
def a_cold_cache():
    machine_launcher._shipped_sources.cache_clear()
    yield
    machine_launcher._shipped_sources.cache_clear()


def test_launching_twice_reads_the_sources_once():
    first = machine_launcher.launch_script(command="echo one")
    second = machine_launcher.launch_script(command="echo two")

    info = machine_launcher._shipped_sources.cache_info()
    assert info.misses == 1, f"read {info.misses} times for two launches"
    assert info.hits == 1
    assert first != second, "the two commands must still reach their scripts"


def test_the_sources_are_the_real_files():
    cli, forge, environment, tunnel, preview = machine_launcher._shipped_sources()

    assert cli.startswith("#!/usr/bin/env python3") and len(cli) > 100_000, len(cli)
    assert forge.startswith("#!/usr/bin/env python3"), forge[:40]
    for helper in (environment, tunnel, preview):
        assert helper.endswith("\n")
