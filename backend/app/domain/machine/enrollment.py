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
import json
import logging
import os
import tempfile
from pathlib import Path

from app.core.config import settings
from app.domain.agent import connector_build
from app.domain.agent.harness.claude_code import (
    CLAUDE_MIN_VERSION,
    CLAUDE_PINNED_VERSION,
    build_startup_cache_prepare,
    build_warm_session_prepare,
)
from app.domain.agent.harness.pi import device_launch as pi_launch
from app.domain.machine import claude_dist

logger = logging.getLogger("cheese.machine.enrollment")

# One attempt should be slow enough to survive a cold apt/curl on a fresh
# machine, and fast enough that a stuck one doesn't wedge the sweep.
SSH_TIMEOUT_S = 180.0

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


def bootstrap_script(
    *, origin: str, token: str, device_id: str, prepare_native_session: bool = False
) -> str:
    """What runs on the machine. Writes the cli's config, then connects.

    Deliberately arch-agnostic: the same script serves an LXC container and a VM
    on either architecture, because the download target is resolved on the
    machine rather than guessed from here.
    """
    config = json.dumps(
        {
            "base": f"{origin.rstrip('/')}/connector",
            "token": token,
            "device_id": device_id,
            **(
                {"ws": "ws://127.0.0.1:18080/connector/agent"}
                if settings.microcloud_direct_control
                else {}
            ),
        }
    )
    # The floor and the pin are the launcher's, read from there rather than
    # restated: a machine enrolled against a different number than the launcher
    # enforces is a machine that enrolls cleanly and then runs nothing.
    origin_clean = origin.rstrip("/")
    min_version = CLAUDE_MIN_VERSION
    pinned_version = CLAUDE_PINNED_VERSION
    pi_script = pi_launch.install(home="$HOME", base=origin_clean)
    preparation_script = ""
    if prepare_native_session:
        ca_pem = ""
        if settings.subscription_enabled:
            if not settings.subscription_ca_backend_path.strip():
                raise EnrollmentError(
                    "SUBSCRIPTION_CA_BACKEND_PATH is required for native preparation"
                )
            ca_pem = Path(settings.subscription_ca_backend_path).read_text()
            if not ca_pem.strip():
                raise EnrollmentError("Subscription proxy CA is empty")
        preparation_script = build_startup_cache_prepare(pinned_version)
        preparation_script += build_warm_session_prepare(pinned_version, ca_pem=ca_pem)
    return f"""set -eu
arch=$(uname -m)
case "$arch" in
  x86_64|amd64) target=linux-amd64 ;;
  aarch64|arm64) target=linux-arm64 ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac
# Tools the machine must have before it is worth enrolling. Both failures are
# SILENT if left to be discovered later, which is why they are fatal here:
#   tmux — the connector hosts every session in it and exits immediately
#     without one, yet `link connect` still reports success (systemctl returns
#     before the process falls over);
#   git  — the agent clones the project into its work dir and pushes the topic
#     branch back. Without git the turn runs in an EMPTY directory, reports
#     success, and the work is never seen by anyone.
# Neither is guaranteed by the image: MicroCloud's LXC template lists git but
# not tmux, and the VM template installs neither (it adds only curl + Docker on
# top of a stock Debian cloud image). Depending on which offering a machine came
# from is exactly the kind of assumption that fails quietly.
for tool in tmux git; do
  command -v "$tool" >/dev/null 2>&1 && continue
  sudo -n apt-get install -y -q "$tool" >/dev/null 2>&1 \
    || {{ sudo -n apt-get update -q >/dev/null 2>&1 \
          && sudo -n apt-get install -y -q "$tool" >/dev/null 2>&1; }} \
    || {{ echo "$tool is missing and could not be installed" >&2; exit 1; }}
done
mkdir -p "$HOME/.local/bin" "$HOME/.config/cheese"
export PATH="$HOME/.local/bin:$PATH"
# claude — the agent itself, and the same argument as tmux/git above with one
# more turn of the screw. A machine with no claude, or one too old, does not
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
# same reason cheesehost is fetched from us two lines below.
#
# Platform strings are the VENDOR's, and the musl check matters: Alpine-style
# machines need a different build, and only the machine can tell.
case "$arch" in
  x86_64|amd64) carch=x64 ;;
  aarch64|arm64) carch=arm64 ;;
esac
if [ "$(uname -s)" = "Linux" ]; then
  if ldd /bin/ls 2>&1 | grep -q musl; then
    cplat="linux-$carch-musl"
  else
    cplat="linux-$carch"
  fi
else
  cplat="darwin-$carch"
fi
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
if [ ! -x "$claude_pin" ]; then
  mkdir -p "$HOME/.cheese/claude/versions"
  curl -fsSL --retry 3 --retry-delay 2 -m 300 \
    "{origin_clean}/connector/claude/{pinned_version}/$cplat/claude" \
    -o "$claude_pin.new" \
    || {{ echo "could not download claude {pinned_version} for $cplat" >&2; exit 1; }}
  test -s "$claude_pin.new"
  chmod +x "$claude_pin.new"
  mv "$claude_pin.new" "$claude_pin"
fi
# Verify what we placed, not what `claude` resolves to — the owner's entry point
# is none of our business and could be any version at all.
have=$("$claude_pin" --version 2>/dev/null | head -n 1 | awk '{{print $1}}')
if [ -z "$have" ] || [ "$(printf '%s\n%s\n' "{min_version}" "$have" \
    | sort -V | head -n 1)" != "{min_version}" ]; then
  echo "claude at $claude_pin is ${{have:-unusable}}, need >= {min_version}" >&2
  exit 1
fi
# pi, the second harness a room can ask for, placed by the same rule and from
# the same platform route. Fatal for the reason the claude check above is: a
# machine that enrols green advertises capacity for every harness we run, and
# the first room to ask for pi is a bad place to discover it never had any.
#
# It is also the one check that can fail on a machine claude is fine on — the
# vendor publishes no musl build — and that is exactly the case worth hearing
# about here rather than reading out of one room's launcher output.
{pi_script}
umask 077
{preparation_script}
mkdir -p "$HOME/.local/bin" "$HOME/.config/cheese"
curl -fsSL --retry 3 --retry-delay 2 -m 120 \\
  "{origin.rstrip("/")}/connector/latest/$target/cheesehost" \\
  -o "$HOME/.local/bin/cheesehost.new"
test -s "$HOME/.local/bin/cheesehost.new"
chmod +x "$HOME/.local/bin/cheesehost.new"
mv "$HOME/.local/bin/cheesehost.new" "$HOME/.local/bin/cheesehost"
# The credential the device flow would have stored. umask first: this file is
# the machine's identity to the platform.
umask 077
cat > "$HOME/.config/cheese/config.json" <<'CHEESE_CONFIG_EOF'
{config}
CHEESE_CONFIG_EOF
# Already logged in as far as the cli is concerned, so this only installs and
# starts the background service — for THIS account, no root involved.
# stdin is closed: this script arrives ON stdin, and anything the command reads
# from it would eat the rest of the script.
"$HOME/.local/bin/cheesehost" link connect < /dev/null
# `link connect` succeeds as soon as systemd accepts the start, which is BEFORE
# the process can fail. Enrollment must not report success for a service that is
# already dead, so ask systemd what actually happened. It is a --user unit, and
# the system manager knows nothing about those.
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
# the same passwordless sudo installed tmux above. `enable-linger` needs it only
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
# The machine's identity at ccproxy, exactly as MicroCloud wrote it into the
# machine's own settings.json. The meter has to present THIS identity to relay
# THIS machine's ticket (ccproxy scopes the swap to the authenticated
# connection), and reading it here costs nothing: we are already on the machine
# over ssh. Best-effort by design — MicroCloud writes that file when the AI
# channel settles, which can be after enrollment runs, so a machine that has no
# file yet is backfilled by the sweep rather than failing to enroll.
python3 - <<'CHEESE_UPSTREAM_EOF' || true
import json, os, urllib.parse
try:
    env = json.load(open(os.path.expanduser("~/.claude/settings.json")))["env"]
    parts = urllib.parse.urlsplit(env.get("HTTPS_PROXY") or "")
except Exception:
    raise SystemExit(0)
if parts.username and parts.password:
    print("CHEESE_CCPROXY_UPSTREAM=%s:%s" % (parts.username, parts.password))
CHEESE_UPSTREAM_EOF
"""


async def run_bootstrap(
    *, ip: str, login_user: str, private_key: str, script: str
) -> str:
    """Run the bootstrap over SSH and return its output.

    The script is fed on stdin rather than passed as an argument: it carries the
    device token, and an argument would show up in the machine's process list.
    """
    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "bootstrap")
        with open(os.open(key_path, os.O_CREAT | os.O_WRONLY, 0o600), "w") as handle:
            handle.write(private_key)
        ssh = ["ssh", "-i", key_path, *SSH_OPTS, f"{login_user}@{ip}"]

        async def run(*command: str) -> bytes:
            child = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    child.communicate(), timeout=SSH_TIMEOUT_S
                )
            except TimeoutError as exc:
                child.kill()
                await child.wait()
                raise EnrollmentError("Claude transfer timed out") from exc
            if child.returncode:
                raise EnrollmentError(
                    f"Claude transfer failed ({child.returncode}): "
                    f"{output.decode()[-300:]}"
                )
            return output

        # Cloud guests can reach us over SSH while their public HTTP download is
        # too slow for enrollment. Reuse the platform's verified cache and the
        # existing bootstrap credential; no extra listener or guest credential.
        pin = CLAUDE_PINNED_VERSION
        remote_dir = ".cheese/claude/versions"
        facts = (
            (
                await run(
                    *ssh,
                    "uname -m; if ldd /bin/ls 2>&1 | grep -q musl; "
                    "then echo musl; else echo glibc; fi; "
                    f'mkdir -p "$HOME/{remote_dir}"; '
                    f'if test -x "$HOME/{remote_dir}/{pin}"; then echo present; fi',
                )
            )
            .decode()
            .splitlines()
        )
        if "present" not in facts:
            arch = {
                "x86_64": "x64",
                "amd64": "x64",
                "aarch64": "arm64",
                "arm64": "arm64",
            }.get(facts[0] if facts else "")
            if arch is None:
                raise EnrollmentError("unsupported cloud machine architecture")
            platform = f"linux-{arch}" + ("-musl" if "musl" in facts else "")
            try:
                binary = await claude_dist.ensure_cached(
                    connector_build.dist_dir(), pin, platform
                )
            except claude_dist.ClaudeDistError as exc:
                raise EnrollmentError("platform Claude binary unavailable") from exc
            await run(
                "scp",
                "-i",
                key_path,
                *SSH_OPTS,
                str(binary),
                f"{login_user}@{ip}:{remote_dir}/{pin}.ssh-new",
            )
            await run(
                *ssh,
                f'chmod +x "$HOME/{remote_dir}/{pin}.ssh-new" && '
                f'mv "$HOME/{remote_dir}/{pin}.ssh-new" "$HOME/{remote_dir}/{pin}"',
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
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(script.encode()), timeout=SSH_TIMEOUT_S
            )
        except TimeoutError as exc:
            process.kill()
            raise EnrollmentError(
                f"bootstrap timed out after {SSH_TIMEOUT_S:.0f}s"
            ) from exc
    output = stdout.decode(errors="replace").strip()
    if process.returncode != 0:
        raise EnrollmentError(
            f"bootstrap failed ({process.returncode}): {output[-400:]}"
        )
    return output


def combine_authorized_keys(*keys: str | None) -> str:
    """authorized_keys is one key per line, and MicroCloud writes what it is
    given verbatim — so the platform's bootstrap key and the human's own key can
    both be authorised. Enrolling a machine must not cost the human their access
    to it."""
    seen: list[str] = []
    for key in keys:
        cleaned = (key or "").strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return "\n".join(seen)


CCPROXY_UPSTREAM_MARKER = "CHEESE_CCPROXY_UPSTREAM="


def parse_ccproxy_upstream(output: str) -> str | None:
    """The `user:password` the bootstrap read off the machine, or None.

    A marker line rather than a second ssh round trip. None covers every way it
    can be absent — machine wired for a different supply, AI channel still
    settling, python missing — because none of those is an enrollment failure;
    the machine is a perfectly good agent host either way, it just falls back to
    the deployment-wide upstream until the sweep fills this in.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(CCPROXY_UPSTREAM_MARKER):
            value = stripped[len(CCPROXY_UPSTREAM_MARKER) :].strip()
            # A bare "user:" or ":password" is not usable as proxy auth, and
            # storing half a credential would fail later, further from here.
            head, sep, tail = value.partition(":")
            return value if (head and sep and tail) else None
    return None


def redact(text: str, *secrets: str) -> str:
    """Never let a token reach a log or an error surfaced to a caller."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
