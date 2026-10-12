"""Execute enrollment with local SSH/SCP transports and a verified-cache fixture."""

import asyncio
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
    seen = []
    released = home / "progress-observed"

    async def progress(text):
        seen.append(text)
        if text == "正在检查并安装基础工具":
            released.touch()

    streaming_script = (
        "echo CHEESE_STARTUP:tools\n"
        "echo CHEESE_STARTUP:secret-token\n"
        'test -e "$HOME/progress-observed" || sleep 0.2\n'
        'test -e "$HOME/progress-observed" || exit 42\n' + script
    )
    output = await enrollment.run_bootstrap(
        ip="guest",
        login_user="cheese",
        private_key="test",
        script=streaming_script,
        progress=progress,
    )
    first = output.splitlines()[-1]
    assert "正在检查并安装基础工具" in seen
    assert all("secret-token" not in item for item in seen)
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
    monkeypatch.setattr(enrollment, "SSH_READY_WAIT_S", 0.3)
    monkeypatch.setattr(enrollment, "SSH_READY_POLL_S", 0.05)
    with pytest.raises(enrollment.EnrollmentError, match="Claude transfer failed"):
        await enrollment.run_bootstrap(
            ip="guest", login_user="cheese", private_key="test", script="exit 0"
        )


async def test_ssh_is_retried_on_its_own_failure_until_the_window_closes(
    tmp_path, monkeypatch
):
    """255 is ssh's own failure: tried again every poll until the wait runs
    out, and the last attempt's output is what the error reports."""
    tries = tmp_path / "tries"
    ssh = tmp_path / "ssh"
    ssh.write_text(
        f'#!/bin/sh\necho x >> "{tries}"\n'
        f'echo "no route $(wc -l < "{tries}")"\nexit 255\n'
    )
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(enrollment, "SSH_READY_WAIT_S", 0.5)
    monkeypatch.setattr(enrollment, "SSH_READY_POLL_S", 0.1)
    with pytest.raises(enrollment.EnrollmentError) as failed:
        await enrollment.run_bootstrap(
            ip="guest", login_user="cheese", private_key="test", script="exit 0"
        )
    attempts = len(tries.read_text().splitlines())
    assert 3 <= attempts <= 7
    assert f"(255): no route {attempts}" in str(failed.value)


async def test_a_command_failing_after_ssh_is_up_is_not_retried(tmp_path, monkeypatch):
    tries = tmp_path / "tries"
    ssh = tmp_path / "ssh"
    ssh.write_text(f'#!/bin/sh\necho x >> "{tries}"\nexit 7\n')
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(enrollment, "SSH_READY_POLL_S", 0.05)
    with pytest.raises(enrollment.EnrollmentError, match=r"\(7\)"):
        await enrollment.run_bootstrap(
            ip="guest", login_user="cheese", private_key="test", script="exit 0"
        )
    assert len(tries.read_text().splitlines()) == 1


async def test_a_machine_whose_ssh_comes_up_late_enrolls_in_one_attempt(
    tmp_path, monkeypatch
):
    home = tmp_path / "guest"
    (home / ".cheese/claude/versions").mkdir(parents=True)
    pin = home / ".cheese/claude/versions" / enrollment.CLAUDE_PINNED_VERSION
    pin.write_text("#!/bin/sh\n")
    pin.chmod(0o755)
    up = tmp_path / "sshd-up"
    ssh = tmp_path / "ssh"
    # Like a guest MicroCloud already reports running: ssh cannot connect yet.
    ssh.write_text(
        f"""#!/bin/bash
if ! test -e "{up}"; then
  echo "ssh: connect to host guest port 22: Connection timed out"
  exit 255
fi
for last; do :; done
export HOME="{home}"
if [ "$last" = 'bash -l -s' ]; then exec /bin/bash -s; fi
exec /bin/bash -c "$last"
"""
    )
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(enrollment, "SSH_READY_POLL_S", 0.1)

    async def sshd_starts():
        await asyncio.sleep(0.5)
        up.touch()

    starting = asyncio.create_task(sshd_starts())
    output = await enrollment.run_bootstrap(
        ip="guest", login_user="cheese", private_key="test", script="echo Connected."
    )
    await starting
    assert output == "Connected."
