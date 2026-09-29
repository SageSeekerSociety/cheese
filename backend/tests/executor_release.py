"""What a session's executor answers a ping with, for a fake machine."""

import hashlib

from app.domain.agent.harness.claude_code.remote_execution import launch, runtime


def running(release_files: dict[str, str] | None = None) -> dict:
    """An executor installed from ``release_files`` — this release unless
    given — reports it as the hash of each file."""
    if release_files is None:
        release_files = launch.file_sources()
    return {
        "capabilities": ["prepare"],
        "protocol_version": runtime.PROTOCOL_VERSION,
        "files": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in release_files.items()
        },
    }
