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


def bootstrap_script(*, origin: str, token: str, device_id: str) -> str:
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
        }
    )
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
# starts the background service (it elevates with sudo by itself).
# stdin is closed: this script arrives ON stdin, and sudo would otherwise eat
# what is left of it — or block on a password prompt with nothing to read.
"$HOME/.local/bin/cheesehost" link connect < /dev/null
# `link connect` succeeds as soon as systemd accepts the start, which is BEFORE
# the process can fail. Enrollment must not report success for a service that is
# already dead, so ask systemd what actually happened.
sleep 5
if ! systemctl is-active --quiet cheese; then
  echo "the connector service did not stay up:" >&2
  sudo -n journalctl -u cheese --no-pager -n 20 >&2 2>/dev/null || true
  exit 1
fi
echo "cheese.service active"
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


def redact(text: str, *secrets: str) -> str:
    """Never let a token reach a log or an error surfaced to a caller."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
