"""The subscription renewer parses the App's key once, not once a minute.

`jwt.encode` turns a PEM string into a key object on every call: about 65 ms,
measured here, against about 1 ms of signing. The renewer signs once a minute
for the life of the process, on the event loop, so the parse is the whole cost
and it is paid on the loop.
"""

import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.forge_events import verify_subscriptions
from app.domain.review import events


def _keypair(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    path = tmp_path / "app.pem"
    path.write_text(private)
    return path, public


async def _no_wait(_seconds):
    return None


class _Socket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


@pytest.mark.anyio
async def test_every_renewal_signs_with_the_key_parsed_once(tmp_path, monkeypatch):
    path, public = _keypair(tmp_path)
    monkeypatch.setattr(events.settings, "github_app_id", 123)
    monkeypatch.setattr(events.settings, "github_app_private_key_path", str(path))
    monkeypatch.setattr(events.settings, "forge_event_github_app_id", 123)
    monkeypatch.setattr(
        events.settings, "forge_event_relay_url", "wss://relay.invalid/second/connect"
    )

    parses = 0
    real = events.signing_key

    def counted(pem):
        nonlocal parses
        parses += 1
        return real(pem)

    monkeypatch.setattr(events, "signing_key", counted)

    class Sessions:
        def __call__(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _query):
            class Rows:
                @staticmethod
                def all():
                    return [(11, "Owner/project")]

            return Rows()

    # The interval is not the behaviour under test, only that more than one
    # renewal happens; the loop's own 60 s is not waited out here.
    monkeypatch.setattr(events, "RENEW_INTERVAL_S", 0.01)

    socket = _Socket()
    task = asyncio.create_task(events.register_subscriptions(socket, Sessions()))
    for _ in range(200):
        if len(socket.sent) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert parses == 1, (
        f"the key was parsed {parses} times for {len(socket.sent)} renewals"
    )
    assert len(socket.sent) >= 2
    for message in socket.sent:
        repositories, _expires = verify_subscriptions(
            message["assertion"], app_id=123, public_key=public, deployment="second"
        )
        assert repositories == [(11, "owner/project")]


def test_a_key_that_is_not_rsa_is_refused_where_it_is_read():
    """The check has to survive `python -O`: it is on configuration, not an
    invariant of the code, so it is a raise and not an assert."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from app.core.forge_events import signing_key

    other = ed25519.Ed25519PrivateKey.generate()
    pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(ValueError):
        signing_key(pem)
