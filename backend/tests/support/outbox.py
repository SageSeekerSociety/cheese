"""A mail sender that keeps what it is asked to send, for tests that read the
code or link out of a mail the API sent."""

import asyncio
import re
import time

import pytest


class Outbox:
    def __init__(self, *, delivers: bool = True) -> None:
        self.is_configured = True
        self.delivers = delivers
        self.held = False
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> bool:
        while self.held:
            await asyncio.sleep(0.01)
        self.sent.append(kwargs)
        return self.delivers

    def wait_for(self, count: int, timeout: float = 5.0) -> None:
        """Some mail leaves after the response, so wait for it to land."""
        deadline = time.monotonic() + timeout
        while len(self.sent) < count:
            assert time.monotonic() < deadline, f"{len(self.sent)} of {count} sent"
            time.sleep(0.01)

    def settle(self, count: int) -> None:
        """Wait for ``count`` mails, then make sure no further one follows."""
        self.wait_for(count)
        time.sleep(0.2)
        assert len(self.sent) == count, self.sent

    def code(self, index: int = -1) -> str:
        """The six-digit code in a sent mail, the last one by default."""
        match = re.search(r"\b(\d{6})\b", self.sent[index]["body_text"])
        assert match, self.sent[index]
        return match.group(1)


def install_outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    import app.core.email as email_module
    import app.domain.user.verification_service as verification_module

    box = Outbox()
    monkeypatch.setattr(email_module, "get_email_sender", lambda: box)
    monkeypatch.setattr(verification_module, "get_email_sender", lambda: box)
    return box
