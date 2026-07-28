#!/usr/bin/env python3
"""End-to-end smoke against MicroCloud: provision a machine and SSH into it.

Proves the tenant-side flow of the MicroCloud contract (see the repo's
`business-model.md`): customer -> fund account -> offering -> machine -> SSH.

Design notes:
- stdlib only, so it runs anywhere inside the ghg network (the dev box, a CI
  runner on that box, or a laptop on the split-tunnel VPN) with no install step.
- Resumable: every created id is checkpointed to a state file, so a re-run
  reuses the customer/account/machine instead of provisioning duplicates.
- Observable: every step is logged with a timestamp to both stdout and a file.

Usage:
    export MICROCLOUD_BASE=http://microcloud-prod.119net.ghg.org.cn
    export MICROCLOUD_TENANT_SECRET=...
    python3 microcloud_smoke.py --hostname cheese-smoke-1

    # tear the machine down again
    python3 microcloud_smoke.py --destroy
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# State (ids + the smoke keypair) must OUTLIVE the workspace: a CI checkout
# wipes untracked files, which would otherwise orphan a provisioned machine on
# every run. Override with MICROCLOUD_SMOKE_STATE_DIR.
STATE_DIR = Path(
    os.environ.get("MICROCLOUD_SMOKE_STATE_DIR")
    or Path(__file__).resolve().parents[1] / "tmp"
)
STATE_PATH = STATE_DIR / "microcloud-smoke-state.json"
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "microcloud-smoke.log"
KEY_PATH = STATE_DIR / "microcloud-smoke-key"

# Terminal states of the async machine lifecycle (contract: MachineStatus).
TERMINAL = {"running", "stopped", "error", "deleted"}
# The AI setup lifecycle runs alongside it and settles separately. `None` covers
# a MicroCloud old enough not to report it at all.
AI_TERMINAL = {"disabled", "ready", "error", None}

log = logging.getLogger("microcloud-smoke")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    fileh = logging.FileHandler(LOG_PATH)
    fileh.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(stream)
    log.addHandler(fileh)


class MicroCloud:
    """Thin tenant-realm client. Every call carries the tenant secret."""

    def __init__(self, base: str, secret: str, timeout: int = 60) -> None:
        self._base = base.rstrip("/") + "/microcloud"
        self._secret = secret
        self._timeout = timeout

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._secret}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def ensure_key() -> str:
    """A dedicated smoke keypair — never reuse a human's personal key."""
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "microcloud-smoke",
             "-f", str(KEY_PATH)],
            check=True, capture_output=True,
        )
        log.info("generated smoke keypair at %s", KEY_PATH)
    return KEY_PATH.with_suffix(".pub").read_text().strip()


def ensure_customer(mc: MicroCloud, state: dict[str, Any], external_ref: str) -> int:
    if state.get("customer_id"):
        try:
            mc.get(f"/customer/{state['customer_id']}")
            return int(state["customer_id"])
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            log.info("checkpointed customer %s is gone; re-creating", state["customer_id"])
            state.pop("customer_id", None)
            state.pop("account_id", None)
    existing = mc.get("/customer?page_size=100")
    for item in existing.get("items", []):
        if item["externalRef"] == external_ref:
            log.info("reusing customer %s (%s)", item["id"], external_ref)
            state["customer_id"] = item["id"]
            save_state(state)
            return int(item["id"])
    created = mc.post("/customer", {"externalRef": external_ref})
    log.info("created customer %s (%s)", created["id"], external_ref)
    state["customer_id"] = created["id"]
    save_state(state)
    return int(created["id"])


def ensure_account(
    mc: MicroCloud, state: dict[str, Any], customer_id: int, name: str, funds: float
) -> int:
    account_id = state.get("account_id")
    if account_id:
        try:
            mc.get(f"/account/{account_id}")
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            log.info("checkpointed account %s is gone; re-creating", account_id)
            account_id = None
    if not account_id:
        existing = mc.get(f"/account?customer_id={customer_id}&page_size=100")
        for item in existing.get("items", []):
            if item["name"] == name:
                account_id = item["id"]
                log.info("reusing account %s (%s)", account_id, name)
                break
    if not account_id:
        created = mc.post("/account", {"customerId": customer_id, "name": name})
        account_id = created["id"]
        log.info("created account %s (%s)", account_id, name)
    state["account_id"] = account_id
    save_state(state)

    account = mc.get(f"/account/{account_id}")
    if float(account["balance"]) < funds:
        topped = mc.post(
            f"/account/{account_id}/topup",
            {"amount": funds, "remark": "cheese microcloud smoke"},
        )
        log.info("topped up account %s -> balance %s", account_id, topped["balance"])
    else:
        log.info("account %s already funded (balance %s)", account_id, account["balance"])
    return int(account_id)


def pick_offering(mc: MicroCloud, wanted: str | None) -> dict[str, Any]:
    offerings = mc.get("/machine/offering?page_size=100").get("items", [])
    if not offerings:
        raise RuntimeError(
            "tenant has no offerings granted — a super-admin must grant one "
            "(machine type + zone + template) before machines can be created"
        )
    for off in offerings:
        log.info(
            "offering %s: type=%s cores=%s-%s mem=%s-%s disk=%s-%s zone=%s tpl=%s status=%s",
            off["id"], off["machineTypeName"], off["coresMin"], off["coresMax"],
            off["memoryMbMin"], off["memoryMbMax"], off["diskGbMin"], off["diskGbMax"],
            off["zoneName"], off["templateName"], off["status"],
        )
    if wanted:
        for off in offerings:
            if str(off["id"]) == wanted or off["templateName"] == wanted:
                return off
        raise RuntimeError(f"no offering matches {wanted!r}")
    active = [o for o in offerings if o.get("status") == "active"] or offerings
    return active[0]


def ensure_machine(
    mc: MicroCloud,
    state: dict[str, Any],
    *,
    customer_id: int,
    account_id: int,
    offering: dict[str, Any],
    hostname: str,
    login_user: str,
    pubkey: str,
    cores: int,
    memory_mb: int,
    disk_gb: int,
) -> dict[str, Any]:
    if state.get("machine_id"):
        try:
            machine = mc.get(f"/machine/{state['machine_id']}")
            log.info("reusing machine %s (status=%s)", machine["id"], machine["status"])
            return machine
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            log.info("checkpointed machine is gone; provisioning a new one")
            state.pop("machine_id", None)

    # Clamp the requested spec into what this offering's machine type allows —
    # the API rejects anything outside the range, and the range is per-offering.
    def clamp(value: int, lo: str, hi: str) -> int:
        return max(int(offering[lo]), min(int(offering[hi]), value))

    body = {
        "customerId": customer_id,
        "accountId": account_id,
        "hostname": hostname,
        "offeringId": offering["id"],
        "cores": clamp(cores, "coresMin", "coresMax"),
        "memoryMb": clamp(memory_mb, "memoryMbMin", "memoryMbMax"),
        "diskGb": clamp(disk_gb, "diskGbMin", "diskGbMax"),
        "user": login_user,
        "sshPubkey": pubkey,
        # Named explicitly: the API would default both to accountId, but then a
        # deployment could never separate compute spend from AI spend later.
        "newapiAccountId": account_id,
        "ccproxyAccountId": account_id,
    }
    log.info("creating machine %s from offering %s (%s cores / %s MiB / %s GB)",
             hostname, offering["id"], body["cores"], body["memoryMb"], body["diskGb"])
    machine = mc.post("/machine", body)
    state["machine_id"] = machine["id"]
    save_state(state)
    log.info("machine %s accepted, status=%s", machine["id"], machine["status"])
    return machine


def wait_for_machine(mc: MicroCloud, machine_id: int, timeout_s: int) -> dict[str, Any]:
    """Wait for BOTH lifecycles to settle.

    `aiStatus` is independent of `status`: a machine reports `running` while its
    Claude Code is still being wired up, so waiting on `status` alone declares
    success before the machine can do the one job it exists for.
    """
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        machine = mc.get(f"/machine/{machine_id}")
        seen = (machine["status"], machine.get("aiStatus"))
        if seen != last:
            log.info(
                "machine %s: status=%s ai=%s/%s ip=%s",
                machine_id, machine["status"], machine.get("aiMode"),
                machine.get("aiStatus"), machine.get("ip"),
            )
            last = seen
        settled = machine["status"] in TERMINAL and (
            machine.get("aiStatus") in AI_TERMINAL
        )
        if settled:
            return machine
        time.sleep(5)
    raise RuntimeError(f"machine {machine_id} still {last} after {timeout_s}s")


def ssh_check(ip: str, login_user: str, timeout_s: int) -> str:
    """Poll SSH until the machine accepts us, then report what is on it."""
    probe = (
        "echo HOST=$(hostname); echo USER=$(whoami); "
        "echo OS=$(. /etc/os-release && echo $PRETTY_NAME); "
        "echo DOCKER=$(docker --version 2>/dev/null || echo none); "
        "echo CLAUDE=$(claude --version 2>/dev/null || echo none); "
        "echo CLAUDE_ENV=$(printenv ANTHROPIC_BASE_URL 2>/dev/null || echo unset); "
        "echo SUDO=$(sudo -n true 2>/dev/null && echo yes || echo no)"
    )
    cmd = [
        "ssh", "-i", str(KEY_PATH),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        f"{login_user}@{ip}", probe,
    ]
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            log.info("ssh ok on attempt %s", attempt)
            return result.stdout.strip()
        log.info("ssh attempt %s not ready: %s", attempt,
                 result.stderr.strip().splitlines()[-1:] or ["(no stderr)"])
        time.sleep(10)
    raise RuntimeError(f"ssh to {login_user}@{ip} never succeeded within {timeout_s}s")


def ssh_probe(ip: str, login_user: str, cheese_base: str) -> str:
    """What can this machine reach? The integration hinges on two answers:
    can it call cheese back (to enroll as a device), and can it reach the
    internet (to download the connector / an agent CLI)."""
    host = cheese_base.split("//", 1)[-1].split(":")[0].split("/")[0]
    script = (
        f'echo CHEESE=$(curl -s -o /dev/null -w %{{http_code}} -m 8 {cheese_base}/healthz);'
        f' echo CHEESE_INSTALLER=$(curl -s -o /dev/null -w %{{http_code}} -m 8 {cheese_base}/api/connector/install.sh);'
        ' echo INTERNET=$(curl -s -o /dev/null -w %{http_code} -m 8 https://api.github.com);'
        ' echo PROD=$(curl -s -o /dev/null -w %{http_code} -m 8 https://cheese.ruc.edu.cn/api/healthz);'
        ' echo NPM=$(command -v npm || echo none);'
        ' echo ARCH=$(uname -m);'
        ' echo ROUTES="$(ip -4 route | tr \'\\n\' \'|\')";'
        f' echo PING_TARGET=$(ping -c1 -W2 {host} >/dev/null 2>&1 && echo ok || echo fail);'
        ' echo PING_GW=$(ping -c1 -W2 $(ip -4 route show default | awk \'{print $3}\') >/dev/null 2>&1 && echo ok || echo fail);'
        ' echo FIREWALL=$(sudo -n ufw status 2>/dev/null | head -1 || echo unknown)'
    )
    cmd = [
        "ssh", "-i", str(KEY_PATH),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
        f"{login_user}@{ip}", script,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not result.stdout.strip():
        raise RuntimeError(f"probe ssh produced nothing: {result.stderr.strip()[:300]}")
    # A probe is a diagnosis, not an assertion: one failing sub-command (an
    # absent `sudo`, a locked-down firewall query) makes ssh exit non-zero, and
    # discarding everything it DID learn is exactly the wrong trade here.
    if result.returncode != 0:
        log.info("probe exited %s; reporting what it got", result.returncode)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", default="cheese-smoke-1")
    parser.add_argument("--user", default="cheese", help="non-root login user")
    parser.add_argument("--external-ref", default="cheese-smoke",
                        help="the tenant's own user id for this customer")
    parser.add_argument("--funds", type=float, default=1000.0)
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--disk-gb", type=int, default=20)
    parser.add_argument("--offering", default=None,
                        help="offering id or template name; default = first active")
    parser.add_argument("--provision-timeout", type=int, default=900)
    parser.add_argument("--ssh-timeout", type=int, default=300)
    parser.add_argument("--destroy", action="store_true",
                        help="delete the checkpointed machine and exit")
    parser.add_argument("--plan-only", action="store_true",
                        help="reachability + catalog only; create nothing")
    parser.add_argument("--probe", action="store_true",
                        help="ssh the checkpointed machine and report what it can reach")
    # The gateway origin, NOT the box IP: a provisioned machine reaches the
    # former and not the latter, which once read as "machines can't reach cheese".
    parser.add_argument(
        "--cheese-base",
        default="https://cheese-dev-env1-gateway.119net.ghg.org.cn",
        help="the cheese origin the machine must reach to enroll",
    )
    args = parser.parse_args()

    setup_logging()

    base = os.environ.get("MICROCLOUD_BASE")
    secret = os.environ.get("MICROCLOUD_TENANT_SECRET")
    if not base or not secret:
        log.error("MICROCLOUD_BASE and MICROCLOUD_TENANT_SECRET must be set")
        return 2

    mc = MicroCloud(base, secret)
    state = load_state()

    if args.destroy:
        machine_id = state.get("machine_id")
        if not machine_id:
            log.info("no checkpointed machine to destroy")
            return 0
        try:
            mc.delete(f"/machine/{machine_id}")
            log.info("delete requested for machine %s", machine_id)
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            log.info("machine %s was already gone", machine_id)
        state.pop("machine_id", None)
        save_state(state)
        return 0

    log.info("ping: %s", mc.get("/ping"))

    if args.probe:
        last = state.get("last_ok") or {}
        if not last.get("ip"):
            log.error("no checkpointed machine to probe — provision one first")
            return 2
        out = ssh_probe(last["ip"], last["user"], args.cheese_base)
        log.info("reachability from %s:\n%s", last["ip"], out)
        return 0

    if args.plan_only:
        # Pre-flight: prove reachability + auth + a usable catalog before we
        # provision anything real. Creates nothing.
        offering = pick_offering(mc, args.offering)
        log.info("PLAN OK — would provision from offering %s (%s / %s / %s), "
                 "%s cores / %s MiB / %s GB",
                 offering["id"], offering["machineTypeName"], offering["zoneName"],
                 offering["templateName"], offering["coresMin"],
                 offering["memoryMbMin"], offering["diskGbMin"])
        customers = mc.get("/customer?page_size=100").get("items", [])
        log.info("tenant currently has %s customer(s): %s", len(customers),
                 [c["externalRef"] for c in customers][:10])
        machines = mc.get("/machine?page_size=100").get("items", [])
        log.info(
            "tenant currently has %s machine(s): %s",
            len(machines),
            [
                (m["hostname"], m["status"], m.get("aiStatus"), m.get("ip"))
                for m in machines
            ][:10],
        )
        return 0

    pubkey = ensure_key()
    customer_id = ensure_customer(mc, state, args.external_ref)
    account_id = ensure_account(mc, state, customer_id, "compute", args.funds)
    offering = pick_offering(mc, args.offering)
    log.info("using offering %s (%s / %s / %s)", offering["id"],
             offering["machineTypeName"], offering["zoneName"], offering["templateName"])

    machine = ensure_machine(
        mc, state,
        customer_id=customer_id, account_id=account_id, offering=offering,
        hostname=args.hostname, login_user=args.user, pubkey=pubkey,
        cores=args.cores, memory_mb=args.memory_mb, disk_gb=args.disk_gb,
    )
    machine = wait_for_machine(mc, int(machine["id"]), args.provision_timeout)
    if machine["status"] != "running":
        log.error("machine ended in status %s — not provisioned", machine["status"])
        return 1
    if not machine.get("ip"):
        log.error("machine is running but has no IP")
        return 1

    log.info("machine %s running at %s", machine["id"], machine["ip"])
    facts = ssh_check(machine["ip"], args.user, args.ssh_timeout)
    log.info("machine facts:\n%s", facts)

    state["last_ok"] = {
        "machine_id": machine["id"], "ip": machine["ip"],
        "hostname": args.hostname, "user": args.user,
    }
    save_state(state)
    log.info("SMOKE OK — ssh -i %s %s@%s", KEY_PATH, args.user, machine["ip"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
