"""Execute enrollment with local SSH/SCP transports and a verified-cache fixture."""

import os

import pytest

from app.domain.machine import enrollment


async def test_cloud_bootstrap_receives_cached_pin_and_reuses_it(tmp_path, monkeypatch):
    tools = tmp_path / "bin"
    tools.mkdir()
    home = tmp_path / "guest"
    home.mkdir()
    scripts = {
        "ssh": """#!/bin/bash
for last; do :; done
export HOME="$TEST_GUEST_HOME"
if [ "$last" = 'bash -l -s' ]; then exec /bin/bash -s; fi
exec /bin/bash -c "$last"
""",
        "scp": """#!/bin/bash
src="${@: -2:1}"
dst="${@: -1}"
cp "$src" "$TEST_GUEST_HOME/${dst#*:}"
""",
    }
    for name, content in scripts.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_GUEST_HOME", str(home))
    pin = enrollment.CLAUDE_PINNED_VERSION
    cached = tmp_path / "claude"
    cached.write_text(f"#!/bin/sh\necho '{pin} (Claude Code)'\n")

    async def ensure_cached(root, version, platform):
        assert version == pin
        assert platform in {
            "linux-x64",
            "linux-arm64",
            "linux-x64-musl",
            "linux-arm64-musl",
        }
        return cached

    monkeypatch.setattr(enrollment.claude_dist, "ensure_cached", ensure_cached)
    script = f'"$HOME/.cheese/claude/versions/{pin}" --version'
    first = await enrollment.run_bootstrap(
        ip="guest", login_user="cheese", private_key="test", script=script
    )
    assert first == f"{pin} (Claude Code)"
    # Retrying an interrupted enrollment must keep the installed pin, not fetch
    # or overwrite it again. The cache is unavailable on this second call.
    cached.unlink()
    second = await enrollment.run_bootstrap(
        ip="guest", login_user="cheese", private_key="test", script=script
    )
    assert second == first


async def test_failed_ssh_stops_before_running_bootstrap(tmp_path, monkeypatch):
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/bin/sh\nexit 255\n")
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    with pytest.raises(enrollment.EnrollmentError, match="Claude transfer failed"):
        await enrollment.run_bootstrap(
            ip="guest", login_user="cheese", private_key="test", script="exit 0"
        )
