"""A gateway whose certificate the helper will not accept fails the request at
once instead of holding it for the patience window.

On dev the gateway name's certificate expired at 2026-10-06 00:00Z. Every model
request then waited the full 60 s window before failing, claude retried ten
times, and each turn spent about 13 minutes saying nothing before it gave up.
"""

import datetime
import ssl
import time

import pytest

from app.domain.agent import machine_tunnel
from tests.tls_gateway import tls_gateway


def test_an_expired_gateway_certificate_fails_the_request_at_once(tmp_path):
    with tls_gateway(tmp_path, expires_in=datetime.timedelta(days=-1)) as (url, ca):
        started = time.monotonic()
        with pytest.raises(ssl.SSLCertVerificationError, match="expired"):
            machine_tunnel._open_with_patience(
                url, "tok", ca_path=ca, window_s=5.0, start_delay_s=0.05
            )
        assert time.monotonic() - started < 2.0


def test_a_valid_gateway_certificate_still_opens_the_tunnel(tmp_path):
    with tls_gateway(tmp_path, expires_in=datetime.timedelta(days=30)) as (url, ca):
        ws, secret = machine_tunnel._open_with_patience(
            url, "tok", ca_path=ca, window_s=5.0, start_delay_s=0.05
        )
        try:
            assert secret == "tok"
        finally:
            ws.close()
