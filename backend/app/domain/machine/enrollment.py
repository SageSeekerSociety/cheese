"""Turn a provisioned machine into an enrolled cheese device, with nobody there.

The normal way a machine joins is the device flow: a person runs
`cheesehost auth login`, opens a link, and approves. That has no answer when the
platform provisions the machine itself — there is no human at the keyboard and
no browser. So cheese mints the credential on the server side (start + approve,
as the person who asked for the machine) and writes it straight into the cli's
config, which is the same file `auth login` would have produced. `link connect`
then finds itself already logged in and only installs the service.

The way in is a bootstrap keypair cheese generates per machine and authorises
alongside the human's own key. It is a means to this one setup and is erased
from the row the moment enrollment succeeds.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tenacity import AsyncRetrying, RetryCallState, retry_if_result, wait_fixed

from app.core.config import settings
from app.core.sentences import say
from app.domain.agent import connector_build, toolchain
from app.domain.agent.harness.claude_code import (
    CLAUDE_MIN_VERSION,
    CLAUDE_PINNED_VERSION,
)
from app.domain.agent.place import footprint_root
from app.domain.machine import claude_dist, toolchain_dist
from app.domain.machine.microcloud import MicroCloudClient
from app.domain.machine.supply import effective_spec, pick_offering

logger = logging.getLogger("cheese.machine.enrollment")

# One attempt should be slow enough to survive a cold download on a fresh
# machine, and fast enough that a stuck one doesn't wedge the sweep.
SSH_TIMEOUT_S = 180.0

# Only these markers may reach the room; process output can contain credentials.
STARTUP_STEPS = {
    "tools": say("cloudStepTools"),
    "connect": say("cloudStepConnect"),
    "verify": say("cloudStepVerify"),
}

# A machine MicroCloud reports running may not answer SSH yet. On dev every
# first-attempt timeout came exactly ConnectTimeout (10 s) after the first
# connect, and the next attempt 11 s later went through — so wait for SSH here
# rather than spend one of the machine's five enrollment attempts on it.
SSH_READY_WAIT_S = 60.0
SSH_READY_POLL_S = 2.0

SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=no",
    # A freshly provisioned machine has a brand-new host key, and its IP is
    # recycled from a pool — recording it would poison the next machine.
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
]


# What a Linux connector places for its rooms' sandboxes whatever the system
# has (`cli/internal/devenv`), and enrollment hands a cloud machine first.
SANDBOX_TOOLS = ("bubblewrap", "host-tools")


class EnrollmentError(RuntimeError):
    pass


async def generate_keypair() -> tuple[str, str]:
    """A throwaway ed25519 pair: (private key PEM, public key line)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "key")
        process = await asyncio.create_subprocess_exec(
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-q",
            "-C",
            "cheese-bootstrap",
            "-f",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise EnrollmentError(f"ssh-keygen failed: {stderr.decode()[:200]}")
        with open(path) as handle:
            private = handle.read()
        with open(path + ".pub") as handle:
            public = handle.read().strip()
    return private, public


# Where the bootstrap reports the size of the data disk it set up for the
# sandboxes' homes (`DATA_DISK`), in bytes.
DATA_DISK_LINE = re.compile(r"^CHEESE_SANDBOX_DISK_BYTES=([0-9]+)$", re.MULTILINE)

# A pool host's data disk: every sandbox's home, its executor's state and its
# project's package store are on it, each with a project quota of its own
# (`remote_execution/sandbox_host.py`), so one sandbox cannot fill the host for
# the others. MicroCloud attaches it as the VM's scsi1, which the guest names
# by id whatever order the kernel found its disks in. Formatted XFS (reflink on
# by default, so the package managers clone from the store) the first time,
# mounted at /data with project quotas and `discard`, which hands what a
# sandbox deletes back to the thin pool under the VM, and bound over the
# directories the platform keeps homes and stores in, so no path changes.
# mkfs.xfs without -f: a disk that carries any signature is refused, not
# wiped. A host with no such disk is not enrolled: it could not hold a sandbox
# to a disk limit, and no sandbox runs without one.
#
# The mkfs.xfs is the platform's, placed with the host tools before this runs
# (`run_bootstrap`, where `cli/internal/devenv` keeps them), not a package of
# the machine's. It is told the feature set of the newest long-term kernel no
# newer than the machine's own (its `lts_*.conf`): a newer mkfs's defaults can
# make a filesystem an older kernel refuses to mount.
DATA_DISK = """
# Ubuntu from 23.10 lets a program make a user namespace only where an
# AppArmor profile names it, and no profile names the platform's bwrap.
# On a host the platform opened, the platform is the owner who allows it.
if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ]; then
  sudo -n "$HOME/.local/bin/cheesehost" sandbox allow < /dev/null
fi
disk=""
for link in /dev/disk/by-id/*drive-scsi1; do
  [ -e "$link" ] && disk=$(readlink -f "$link")
done
if [ -z "$disk" ]; then
  echo "no data disk (scsi1) for the sandboxes' homes" >&2
  exit 1
fi
tools="$HOME/.cheese/runtime/host-tools"
if [ ! -x "$tools/bin/mkfs.xfs" ]; then
  echo "no platform mkfs.xfs at $tools" >&2
  exit 1
fi
kernel=$(uname -r | cut -d. -f1,2)
features=""
for conf in $(ls "$tools"/mkfs/lts_*.conf | sort -V); do
  lts=${{conf##*/lts_}}
  lts=${{lts%.conf}}
  [ "$(printf '%s\\n%s\\n' "$lts" "$kernel" | sort -V | head -n 1)" = "$lts" ] \\
    && features=$conf
done
if [ -z "$features" ]; then
  echo "kernel $kernel is older than any the platform's mkfs.xfs formats for" >&2
  exit 1
fi
kind=$(sudo -n blkid -o value -s TYPE "$disk" || true)
if [ -z "$kind" ]; then
  sudo -n "$tools/bin/mkfs.xfs" -q -c options="$features" "$disk"
elif [ "$kind" != xfs ]; then
  echo "the data disk $disk holds $kind, not XFS" >&2
  exit 1
fi
uuid=$(sudo -n blkid -o value -s UUID "$disk")
root="$HOME/.cheese"
mkdir -p "$root/home" "$root/store"
mount_line() {{
  grep -qsxF "$1" /etc/fstab || echo "$1" | sudo -n tee -a /etc/fstab >/dev/null
}}
mount_line "UUID=$uuid /data xfs discard,prjquota,nofail 0 0"
mount_line "/data/home $root/home none bind,nofail,x-systemd.requires-mounts-for=/data 0 0"
mount_line "/data/store $root/store none bind,nofail,x-systemd.requires-mounts-for=/data 0 0"
sudo -n systemctl daemon-reload
mountpoint -q /data || {{ sudo -n mkdir -p /data && sudo -n mount /data; }}
sudo -n install -d -o "$(id -u)" -g "$(id -g)" -m 0700 /data/home /data/store
mountpoint -q "$root/home" || sudo -n mount "$root/home"
mountpoint -q "$root/store" || sudo -n mount "$root/store"
set -- $(stat -f -c '%b %S' /data)
echo "CHEESE_SANDBOX_DISK_BYTES=$(( $1 * $2 ))"
"""


def sandbox_disk_gb(output: str) -> int:
    """The data disk's size a bootstrap reported, in GB."""
    found = DATA_DISK_LINE.findall(output)
    if not found:
        # i18n-exempt: an enrollment failure, kept on the host row for operators
        raise EnrollmentError("the bootstrap reported no data disk")
    return int(found[-1]) // 10**9


def bootstrap_script(
    *, origin: str, token: str, device_id: str, sandbox_disk: bool
) -> str:
    """What runs on the machine. Writes the cli's config, then connects.
    With ``sandbox_disk``, first sets up the data disk a pool host keeps its
    sandboxes' homes on (``DATA_DISK``); a whole VM runs none.

    The connector is already on the machine: ``run_bootstrap`` put it there
    over the same SSH connection before this runs.
    """
    config = json.dumps(
        {
            "base": f"{origin.rstrip('/')}/connector",
            "token": token,
            "device_id": device_id,
            # :18083 rather than :18080, which the rest of this tunnel uses.
            # Both are forwards `cloud-control.py` opens on the machine, but
            # this one lands on the connection owner instead of api-front.
            #
            # api-front is reloaded by every release (its backend upstream
            # is swapped that way) and a reload retires the old worker,
            # taking the long-lived connections it held with it after
            # `worker_shutdown_timeout`. The control channel was losing a
            # reconnect to that on every deploy while its own destination
            # never moved. The owner is the process deploys deliberately do
            # not recreate, so a link that lands on it directly survives
            # them.
            **(
                {"ws": "ws://127.0.0.1:18083/connector/agent"}
                if settings.microcloud_direct_control
                else {}
            ),
        }
    )
    # The floor and the pin are the launcher's, read from there rather than
    # restated: a machine enrolled against a different number than the launcher
    # enforces is a machine that enrolls cleanly and then runs nothing.
    min_version = CLAUDE_MIN_VERSION
    pinned_version = CLAUDE_PINNED_VERSION
    data_disk = DATA_DISK.format() if sandbox_disk else ""
    return f"""set -eu
mkdir -p "$HOME/.local/bin" "$HOME/.config/cheese"
export PATH="$HOME/.local/bin:$PATH"
# The credential the device flow would have stored. umask first: this file is
# the machine's identity to the platform.
umask 077
cat > "$HOME/.config/cheese/config.json" <<'CHEESE_CONFIG_EOF'
{config}
CHEESE_CONFIG_EOF
# claude — the agent itself. A machine with no claude, or one too old, does not
# fail here: it enrolls cleanly, reports healthy, and then every topic assigned
# to it never starts, because the launcher refuses a build below the floor.
# A loud failure now beats a silent one per topic later (#489, #501 are the
# same shape).
#
# The binary comes from US, not from the vendor. A cloud node sits on a private
# subnet, a self-hosted machine belongs to a user whose network we do not
# control, and the vendor's own installer names "not available in your region"
# as a failure mode. Serving it ourselves is what turns the pin from a hope
# about what the machine downloaded into a fact about what we handed it — the
# same reason the cheesehost this script runs is the one we serve.
#
# The connector installs it, from the server the config above names, the way
# every installer of it on a machine does: the build for this machine's libc,
# continued where a cut-off attempt stopped, checked against the digest the
# server names, one install at a time (`claudecode.Ensure`).
#
# The pinned build lives under Cheese's own directory. The owner's Claude
# installation, version store, and command links remain untouched.
#
# Specifically: no symlink into ~/.local/bin. That path is the machine owner's
# claude, and on a self-hosted machine it belongs to a person who did not ask
# us to change which version they type `claude` and get. The launcher looks in
# versions/<pin> first and never consults ~/.local/bin for the pin, so the
# symlink would buy nothing and cost someone their default.
#
# And we install our pin even when a good-enough claude is already present:
# otherwise the platform silently rides whatever the owner happens to have, and
# their next upgrade or downgrade becomes our behaviour change. Pinning has to
# mean the version we put there, not the version we found.
claude_pin="$HOME/.cheese/claude/versions/{pinned_version}"
"$HOME/.local/bin/cheesehost" claude install < /dev/null > /dev/null \
  || {{ echo "could not install claude {pinned_version}" >&2; exit 1; }}
# Verify what we placed, not what `claude` resolves to — the owner's entry point
# is none of our business and could be any version at all.
have=$("$claude_pin" --version 2>/dev/null | head -n 1 | awk '{{print $1}}')
if [ -z "$have" ] || [ "$(printf '%s\n%s\n' "{min_version}" "$have" \
    | sort -V | head -n 1)" != "{min_version}" ]; then
  echo "claude at $claude_pin is ${{have:-unusable}}, need >= {min_version}" >&2
  exit 1
fi
# Already logged in as far as the cli is concerned, so this only installs and
# starts the background service — for THIS account, no root involved.
# stdin is closed: this script arrives ON stdin, and anything the command reads
# from it would eat the rest of the script.
echo CHEESE_STARTUP:connect
"$HOME/.local/bin/cheesehost" link connect < /dev/null
# What the connector and its rooms run on, which `link connect` just placed
# where the system's own is missing or too old (`cli/internal/devenv`): tmux,
# which the connector keeps every screen in and exits without, while systemd
# still reports it started; git, without which a room's turn runs in an empty
# directory and reports success; python3, which the platform's own programs on
# a machine are. Nothing comes from the machine's packages, so a machine where
# one could not be placed is refused here, while somebody reads the output.
echo CHEESE_STARTUP:tools
runtime="$HOME/.cheese/runtime"
for tool in tmux git python3; do
  case "$tool" in python3) dir=python ;; *) dir=$tool ;; esac
  PATH="$runtime/$dir/bin:$PATH" command -v "$tool" >/dev/null 2>&1 \\
    || {{ echo "$tool is missing and cheesehost could not place it" >&2; exit 1; }}
done
{data_disk}
# A retried enrollment writes a new device identity. Starting an active service
# leaves the old token in memory, so the room waits for a device that never joins.
systemctl --user restart cheese
# `link connect` succeeds as soon as systemd accepts the start, which is BEFORE
# the process can fail. Enrollment must not report success for a service that is
# already dead, so ask systemd what actually happened. It is a --user unit, and
# the system manager knows nothing about those.
echo CHEESE_STARTUP:verify
sleep 5
if ! systemctl --user is-active --quiet cheese; then
  echo "the connector service did not stay up:" >&2
  journalctl --user -u cheese --no-pager -n 20 >&2 2>/dev/null || true
  exit 1
fi
# Nobody ever logs into this machine, and the user manager holding the connector
# is torn down with the last session — this ssh one — unless the account
# lingers. `link connect` turns it on and says so when it cannot, but that is a
# warning on a stream nobody reads: without this check the machine enrols, goes
# green, and drops off the moment we disconnect.
#
# The connector asks for itself and never elevates, because the machine it runs
# on is usually somebody's laptop. This one is not: the platform opened it, and
# the same passwordless sudo set up its data disk. `enable-linger` needs it only
# where the image ships no polkit — logind then denies the unprivileged request
# outright — so try once, and judge on what linger actually says afterwards.
if [ "$(loginctl show-user "$(id -un)" -p Linger 2>/dev/null)" != "Linger=yes" ]; then
  sudo -n loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
fi
if [ "$(loginctl show-user "$(id -un)" -p Linger 2>/dev/null)" != "Linger=yes" ]; then
  echo "linger is off for $(id -un): the connector would stop when this ssh" >&2
  echo "session ends and never return after a reboot." >&2
  exit 1
fi
echo "cheese.service active"
"""


#: Characters of an image version kept: wide enough that two versions do not
#: collide by accident, short enough to read on the admin page.
IMAGE_VERSION_CHARS = 16

#: What stands in for the parts of the script that name ONE machine (its
#: origin, token and device), so a version can be taken of what every machine
#: gets rather than of one machine's credentials.
_VERSION_PLACEHOLDER = "image-version-placeholder"


def bootstrap_digest(*, sandbox_disk: bool) -> str:
    """A digest of the script a host enrolls with, with its per-machine
    credentials held at a placeholder: a change to anything the machine is set
    up with changes the digest, and no token can ever reach it."""
    script = bootstrap_script(
        origin=_VERSION_PLACEHOLDER,
        token=_VERSION_PLACEHOLDER,
        device_id=_VERSION_PLACEHOLDER,
        sandbox_disk=sandbox_disk,
    )
    return hashlib.sha256(script.encode()).hexdigest()


def host_image_version(
    offering: dict[str, Any], *, cores: int, memory_mb: int, disk_gb: int
) -> str:
    """What a host created now would be made of, as one value a host row keeps
    and the sweep compares it against: the offering and its template, the size
    the machine gets, its login user, and the script it enrolls with. Change any
    of those and a host enrolled before is out of date, due to be rolled."""
    payload = {
        "offering": [
            offering.get(key)
            for key in (
                "id",
                "machineTypeName",
                "zoneName",
                "templateName",
                "dataDiskGb",
            )
        ],
        "spec": [int(cores), int(memory_mb), int(disk_gb)],
        "user": settings.microcloud_login_user,
        "bootstrap": bootstrap_digest(sandbox_disk=True),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:IMAGE_VERSION_CHARS]


async def current_host_image_version(client: MicroCloudClient | None = None) -> str:
    """The version a host created right now would record: the pool's offering
    today, the size it asks for today, and the script it would enroll with.
    Reads the provider, so call it with no lock held."""
    offering = await pick_offering(client or MicroCloudClient())
    spec = effective_spec(
        offering,
        cores=settings.microcloud_default_cores,
        memory_mb=settings.microcloud_default_memory_mb,
        disk_gb=settings.microcloud_default_disk_gb,
    )
    return host_image_version(
        offering,
        cores=spec["cores"],
        memory_mb=spec["memoryMb"],
        disk_gb=spec["diskGb"],
    )


def _last_result(state: RetryCallState):
    """What the last attempt returned, once retrying it has run out."""
    assert state.outcome is not None
    return state.outcome.result()


async def run_bootstrap(
    *,
    ip: str,
    login_user: str,
    private_key: str,
    script: str,
    sandboxes: bool,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Run the bootstrap over SSH and return its output. With ``sandboxes``
    (a pool host, whose rooms run in sandboxes), first hand the machine what
    those are made with (`SANDBOX_TOOLS`); a whole VM runs none.

    The script is fed on stdin rather than passed as an argument: it carries the
    device token, and an argument would show up in the machine's process list.
    """
    with tempfile.TemporaryDirectory() as tmp:
        if progress:
            await progress(say("cloudStepChecking"))
        key_path = os.path.join(tmp, "bootstrap")
        with open(os.open(key_path, os.O_CREAT | os.O_WRONLY, 0o600), "w") as handle:
            handle.write(private_key)
        ssh = ["ssh", "-i", key_path, *SSH_OPTS, f"{login_user}@{ip}"]

        async def once(
            *command: str, stdin: Path | None = None
        ) -> tuple[int | None, bytes]:
            child = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    child.communicate(stdin.read_bytes() if stdin else None),
                    timeout=SSH_TIMEOUT_S,
                )
            except TimeoutError as exc:
                child.kill()
                await child.wait()
                raise EnrollmentError("transfer to the machine timed out") from exc
            return child.returncode, output

        async def run(
            *command: str, wait_for_ssh: bool = False, stdin: Path | None = None
        ) -> bytes:
            deadline = asyncio.get_running_loop().time() + SSH_READY_WAIT_S
            returncode, output = await AsyncRetrying(
                # 255 is ssh's own failure — the connection, not the command.
                retry=retry_if_result(lambda ran: wait_for_ssh and ran[0] == 255),
                stop=lambda _: asyncio.get_running_loop().time() >= deadline,
                wait=wait_fixed(SSH_READY_POLL_S),
                retry_error_callback=_last_result,
            )(once, *command, stdin=stdin)
            if returncode:
                raise EnrollmentError(
                    f"transfer to the machine failed ({returncode}): "
                    f"{output.decode()[-300:]}"
                )
            return output

        # Cloud guests can reach us over SSH while their public HTTP download is
        # too slow for enrollment: on dev a guest resolves the public origin to a
        # gateway proxy's address and fetched the 32 MB connector at 3 MB/s,
        # where the same file took 0.4 s over this connection. So the binaries
        # the machine needs before it can talk to us go over the bootstrap
        # credential, from the files the platform serves and caches; no extra
        # listener or guest credential.
        pin = CLAUDE_PINNED_VERSION
        remote_dir = ".cheese/claude/versions"
        facts = (
            (
                await run(
                    *ssh,
                    "uname -m; if ldd /bin/ls 2>&1 | grep -q musl; "
                    "then echo musl; else echo glibc; fi; "
                    f'mkdir -p "$HOME/{remote_dir}" "$HOME/.local/bin"; '
                    f'if test -x "$HOME/{remote_dir}/{pin}"; then echo present; fi',
                    wait_for_ssh=True,
                )
            )
            .decode()
            .splitlines()
        )
        arch = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }.get(facts[0] if facts else "")
        if arch is None:
            raise EnrollmentError("unsupported cloud machine architecture")

        async def push(source: Path, remote: str) -> None:
            """``source`` to ``remote`` (relative to the login's home), staged
            beside it and renamed, so an interrupted copy is never run."""
            await run(
                "scp",
                "-i",
                key_path,
                *SSH_OPTS,
                str(source),
                f"{login_user}@{ip}:{remote}.ssh-new",
            )
            await run(
                *ssh,
                f'chmod +x "$HOME/{remote}.ssh-new" && '
                f'mv "$HOME/{remote}.ssh-new" "$HOME/{remote}"',
            )

        if "present" not in facts:
            platform = f"linux-{'x64' if arch == 'amd64' else arch}" + (
                "-musl" if "musl" in facts else ""
            )
            try:
                if progress:
                    await progress(say("cloudStepPreparingClaude"))
                binary = await claude_dist.ensure_cached(
                    connector_build.dist_dir(), pin, platform
                )
            except claude_dist.ClaudeDistError as exc:
                raise EnrollmentError("platform Claude binary unavailable") from exc
            if progress:
                await progress(say("cloudStepTransferringClaude"))
            await push(binary, f"{remote_dir}/{pin}")
        # The connector the origin serves for this machine, every time: a retry
        # may find an older one an earlier attempt left. People's own machines
        # and a connector's self-update still download it over HTTP.
        connector = connector_build.binary_path(f"linux-{arch}")
        if connector is None:
            raise EnrollmentError(  # i18n-exempt: an enrollment failure, kept on the host row for operators
                f"the platform serves no connector for linux-{arch}"
            )
        if progress:
            await progress(say("cloudStepConnector"))
        await push(connector, ".local/bin/cheesehost")
        # What `link connect` places next on Linux for the rooms' sandboxes
        # (`cli/internal/devenv`), put where it puts them: the archive unpacked
        # without its top directory into `runtime/<tool>`, and the digest the
        # toolchain route names in `runtime/<tool>.sha256`, which is how the
        # connector tells a placed copy is current and skips the download. The
        # same verified file the route serves, so the connector's check
        # agrees. A machine of a person's own, and a later bump, still fetch
        # over HTTP. Unpacked to a staged directory and renamed, so an
        # interrupted copy is never taken for a placed one.
        runtime = f"$HOME/{footprint_root()}/runtime"
        platform_key = f"linux-{'x64' if arch == 'amd64' else arch}"
        for tool in SANDBOX_TOOLS if sandboxes else ():
            try:
                archive = await toolchain_dist.ensure_cached(
                    connector_build.dist_dir(), tool, platform_key
                )
            except toolchain_dist.ToolchainDistError as exc:
                raise EnrollmentError(  # i18n-exempt: an enrollment failure, kept on the host row for operators
                    f"the platform cannot serve {tool} for {platform_key}"
                ) from exc
            digest = toolchain.ARTIFACTS[(tool, platform_key)].sha256
            placed, staged = f"{runtime}/{tool}", f"{runtime}/{tool}.ssh-new"
            await run(
                *ssh,
                f'set -e; mkdir -p "{runtime}"; rm -rf "{staged}"; '
                f'mkdir -m 700 "{staged}"; '
                f'tar -xzf - -C "{staged}" --strip-components=1; '
                f'rm -rf "{placed}"; mv "{staged}" "{placed}"; '
                f'(umask 077; echo {digest} > "{placed}.sha256")',
                stdin=archive,
            )
        command = [
            "ssh",
            "-i",
            key_path,
            *SSH_OPTS,
            f"{login_user}@{ip}",
            # A login shell: MicroCloud puts claude and the cli's directory on
            # PATH from the profile, which a plain command shell never reads.
            "bash -l -s",
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def collect() -> bytes:
            if progress is None:
                output, _ = await process.communicate(script.encode())
                return output
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(script.encode())
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while line := await process.stdout.readline():
                output.extend(line)
                marker = line.decode(errors="replace").strip()
                if marker.startswith("CHEESE_STARTUP:"):
                    step = STARTUP_STEPS.get(marker.removeprefix("CHEESE_STARTUP:"))
                    if step:
                        await progress(step)
            await process.wait()
            return bytes(output)

        try:
            stdout = await asyncio.wait_for(collect(), timeout=SSH_TIMEOUT_S)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise EnrollmentError(
                f"bootstrap timed out after {SSH_TIMEOUT_S:.0f}s"
            ) from exc
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
    output = stdout.decode(errors="replace").strip()
    if process.returncode != 0:
        raise EnrollmentError(
            f"bootstrap failed ({process.returncode}): {output[-400:]}"
        )
    return output


def combine_authorized_keys(*keys: str | None) -> str:
    """authorized_keys is one key per line, and MicroCloud writes what it is
    given verbatim — so the platform's bootstrap key and the operator's key can
    both be authorised, and a key given twice or not at all is dropped."""
    seen: list[str] = []
    for key in keys:
        cleaned = (key or "").strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return "\n".join(seen)


def redact(text: str, *secrets: str) -> str:
    """Never let a token reach a log or an error surfaced to a caller."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
