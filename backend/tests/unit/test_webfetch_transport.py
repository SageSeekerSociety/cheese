"""Exercise the WebFetch transport's HTTP error propagation."""

import subprocess
from pathlib import Path


def test_webfetch_transport_http_behaviour():
    result = subprocess.run(
        [
            "node",
            "--test",
            str(Path(__file__).with_name("webfetch_transport.test.cjs")),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_private_chats_page_reads_leave_through_its_egress():
    result = subprocess.run(
        [
            "node",
            "--test",
            str(Path(__file__).with_name("webfetch_egress.test.cjs")),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
