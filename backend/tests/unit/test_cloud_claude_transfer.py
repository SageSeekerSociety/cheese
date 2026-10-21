"""Execute enrollment with local SSH/SCP transports and a verified-cache fixture."""

import asyncio
import io
import os
import platform
import tarfile

import pytest

from app.api.routes.installer import download_toolchain
from app.domain.machine import enrollment, toolchain_dist

# The connector target of the machine these tests run on, which the fake ssh
# below makes the guest.
GUEST_TARGET = {
    "x86_64": "linux-amd64",
    "amd64": "linux-amd64",
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
}[platform.machine().lower()]


@pytest.fixture(autouse=True)
def served_connectors(tmp_path, monkeypatch):
    """The connectors the platform serves, each saying which target it is."""
    dist = tmp_path / "connector-dist"
    for target in ("linux-amd64", "linux-arm64"):
        binary = dist / target / "cheesehost"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\necho served {target}\n")
    monkeypatch.setattr(enrollment.connector_build, "dist_dir", lambda: dist)
    return dist


def _archive(path, top, files):
    """A toolchain archive as the releases are cut: everything under ``top/``."""
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            entry = tarfile.TarInfo(f"{top}/{name}")
            entry.size, entry.mode = len(data), 0o755
            archive.addfile(entry, io.BytesIO(data))


@pytest.fixture(autouse=True)
def served_sandbox_tools(tmp_path, monkeypatch):
    """The sandbox tools' archives the toolchain route serves, by platform;
    ``broken`` names a tool the platform cannot get."""
    broken: set[str] = set()

    async def ensure_cached(root, tool, platform_key):
        if tool in broken:
            raise toolchain_dist.ToolchainDistError(f"{tool} unavailable upstream")
        path = tmp_path / "toolchain-cache" / tool / platform_key / "artifact.tar.gz"
        if not path.exists():
            path.parent.mkdir(parents=True)
            program = {"bubblewrap": "bwrap", "host-tools": "bin/mkfs.xfs"}[tool]
            _archive(
                path,
                f"{tool}-1",
                {program: f"#!/bin/sh\necho {tool} {platform_key}\n"},
            )
        return path

    monkeypatch.setattr(toolchain_dist, "ensure_cached", ensure_cached)
    return broken


async def _served_digest(tool, platform_key):
    """The digest the toolchain route answers with, which the connector
    compares with the one beside its placed copy (`cli/internal/devenv`)."""
    response = await download_toolchain(tool, platform_key)
    assert response.status_code == 200
    return response.headers["X-Checksum-SHA256"]


def fake_transports(tools, home):
    """ssh runs its command here with HOME at ``home``; scp copies into it."""
    scripts = {
        "ssh": f"""#!/bin/bash
for last; do :; done
export HOME="{home}"
if [ "$last" = 'bash -l -s' ]; then exec /bin/bash -s; fi
exec /bin/bash -c "$last"
""",
        "scp": f"""#!/bin/bash
src="${{@: -2:1}}"
dst="${{@: -1}}"
cp "$src" "{home}/${{dst#*:}}"
""",
    }
    tools.mkdir(exist_ok=True)
    for name, content in scripts.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)


async def test_cloud_bootstrap_receives_cached_pin_and_reuses_it(tmp_path, monkeypatch):
    tools = tmp_path / "bin"
    home = tmp_path / "guest"
    home.mkdir()
    fake_transports(tools, home)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
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
    with pytest.raises(
        enrollment.EnrollmentError, match="transfer to the machine failed"
    ):
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
    fake_transports(tmp_path / "bin", home)
    monkeypatch.setenv("PATH", f"{tmp_path}:{tmp_path / 'bin'}:{os.environ['PATH']}")
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


def _guest_with_claude(tmp_path):
    home = tmp_path / "guest"
    pin = home / ".cheese/claude/versions" / enrollment.CLAUDE_PINNED_VERSION
    pin.parent.mkdir(parents=True)
    pin.write_text("#!/bin/sh\n")
    pin.chmod(0o755)
    return home


@pytest.mark.parametrize(
    ("machine", "target"),
    [("x86_64", "linux-amd64"), ("aarch64", "linux-arm64")],
)
async def test_the_machine_runs_the_connector_the_platform_serves_for_it(
    tmp_path, monkeypatch, machine, target
):
    """A cloud machine gets the connector over the enrollment connection, the
    build the origin serves for its architecture, before the script runs."""
    home = _guest_with_claude(tmp_path)
    fake_transports(tmp_path / "bin", home)
    # The guest's architecture, whatever this machine's is.
    uname = tmp_path / "bin" / "uname"
    uname.write_text(
        f'#!/bin/sh\n[ "$1" = -m ] && echo {machine} || exec /usr/bin/uname "$@"\n'
    )
    uname.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    seen = []

    async def progress(text):
        seen.append(text)

    output = await enrollment.run_bootstrap(
        ip="guest",
        login_user="cheese",
        private_key="test",
        script='"$HOME/.local/bin/cheesehost"',
        progress=progress,
    )
    assert output == f"served {target}"
    assert "正在下载连接器" in seen


async def test_a_retry_replaces_a_connector_an_earlier_attempt_left(
    tmp_path, monkeypatch
):
    home = _guest_with_claude(tmp_path)
    stale = home / ".local/bin/cheesehost"
    stale.parent.mkdir(parents=True)
    stale.write_text("#!/bin/sh\necho stale\n")
    stale.chmod(0o755)
    fake_transports(tmp_path / "bin", home)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    output = await enrollment.run_bootstrap(
        ip="guest",
        login_user="cheese",
        private_key="test",
        script='"$HOME/.local/bin/cheesehost"',
    )
    assert output == f"served {GUEST_TARGET}"


async def test_no_served_connector_stops_before_the_script(
    tmp_path, monkeypatch, served_connectors
):
    home = _guest_with_claude(tmp_path)
    (served_connectors / GUEST_TARGET / "cheesehost").unlink()
    fake_transports(tmp_path / "bin", home)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    ran = home / "script-ran"
    with pytest.raises(enrollment.EnrollmentError, match=GUEST_TARGET):
        await enrollment.run_bootstrap(
            ip="guest",
            login_user="cheese",
            private_key="test",
            script=f'touch "{ran}"',
        )
    assert not ran.exists()


@pytest.mark.parametrize(
    ("machine", "platform_key"),
    [("x86_64", "linux-x64"), ("aarch64", "linux-arm64")],
)
async def test_the_sandbox_tools_are_placed_where_the_connector_takes_them_as_current(
    tmp_path, monkeypatch, machine, platform_key
):
    """A cloud machine gets bubblewrap and the host tools over the enrollment
    connection, before the script runs, unpacked where its connector keeps
    them and beside the digest the route serves, so `link connect` finds them
    current and downloads neither."""
    home = _guest_with_claude(tmp_path)
    fake_transports(tmp_path / "bin", home)
    uname = tmp_path / "bin" / "uname"
    uname.write_text(
        f'#!/bin/sh\n[ "$1" = -m ] && echo {machine} || exec /usr/bin/uname "$@"\n'
    )
    uname.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    runtime = home / ".cheese/runtime"

    output = await enrollment.run_bootstrap(
        ip="guest",
        login_user="cheese",
        private_key="test",
        script='r="$HOME/.cheese/runtime"; "$r/bubblewrap/bwrap"; '
        '"$r/host-tools/bin/mkfs.xfs"',
    )

    assert output.splitlines() == [
        f"bubblewrap {platform_key}",
        f"host-tools {platform_key}",
    ]
    for tool in ("bubblewrap", "host-tools"):
        marker = (runtime / f"{tool}.sha256").read_text().strip()
        assert marker == await _served_digest(tool, platform_key)
    assert sorted(p.name for p in runtime.iterdir()) == [
        "bubblewrap",
        "bubblewrap.sha256",
        "host-tools",
        "host-tools.sha256",
    ]


async def test_a_retry_replaces_sandbox_tools_an_earlier_attempt_left(
    tmp_path, monkeypatch
):
    """An older placement, or one an interrupted attempt left half made, is
    replaced whole: nothing of it is left to run, and the digest is current."""
    home = _guest_with_claude(tmp_path)
    runtime = home / ".cheese/runtime"
    (runtime / "host-tools/bin").mkdir(parents=True)
    (runtime / "host-tools/bin/old-only").write_text("old")
    (runtime / "host-tools.sha256").write_text("0" * 64 + "\n")
    (runtime / "bubblewrap.ssh-new").mkdir()
    (runtime / "bubblewrap.ssh-new/partial").write_text("half")
    fake_transports(tmp_path / "bin", home)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    platform_key = {"linux-amd64": "linux-x64", "linux-arm64": "linux-arm64"}[
        GUEST_TARGET
    ]

    await enrollment.run_bootstrap(
        ip="guest", login_user="cheese", private_key="test", script="true"
    )

    assert not (runtime / "host-tools/bin/old-only").exists()
    assert not (runtime / "bubblewrap.ssh-new").exists()
    assert (runtime / "host-tools.sha256").read_text().strip() == (
        await _served_digest("host-tools", platform_key)
    )


async def test_a_sandbox_tool_the_platform_cannot_serve_stops_before_the_script(
    tmp_path, monkeypatch, served_sandbox_tools
):
    served_sandbox_tools.add("host-tools")
    home = _guest_with_claude(tmp_path)
    fake_transports(tmp_path / "bin", home)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    ran = home / "script-ran"
    with pytest.raises(enrollment.EnrollmentError, match="host-tools"):
        await enrollment.run_bootstrap(
            ip="guest",
            login_user="cheese",
            private_key="test",
            script=f'touch "{ran}"',
        )
    assert not ran.exists()
