"""What a session on the session host is bounded by.

The session host is what every project's sessions share: one machine, one
kernel. A session there without bounds does not fail alone — the machine
stalls in swap and takes every room on it, and whatever else runs beside them,
down together (#1544).
"""

from app.core.config import settings

#: The variable the launcher reads to start a session under its memory cap.
SESSION_MEMORY_ENV = "CHEESE_SESSION_MEMORY_MAX"


def session_memory_max(device_id: str) -> str | None:
    """The cap a session started on ``device_id`` runs under, as systemd spells
    it; None on any machine but the session host, which is the only one whose
    kernel every room shares."""
    mb = settings.agent_session_memory_max_mb
    if mb <= 0 or device_id != settings.agent_session_device_id:
        return None
    return f"{mb}M"
