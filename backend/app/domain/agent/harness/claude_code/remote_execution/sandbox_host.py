#!/usr/bin/python3 -I
"""Give one session's sandbox its own network and resource limits, as root.

Installed on a Cloud machine as `bootstrap.SANDBOX_HOST` and run by the
machine's connector user through `sudo -n`, once when a sandbox starts and
once when it goes. Everything else about a sandbox is unprivileged
bubblewrap; these two things are not: a network namespace that reaches the
internet needs a veth pair, NAT and filter rules in the machine's own tables,
and a memory or CPU limit needs a cgroup only root can create.

    up NAME PID --memory-mb N --swap-mb N --cpus N --pids N [--forward PORT]...
    down NAME

`PID` is the sandbox's first process, created by bubblewrap with a network
namespace of its own and still waiting to start the session's program. `up`
creates a veth pair with one end in that namespace and puts the process in a
cgroup with the given limits. The sandbox may reach the machine's DNS
resolvers on port 53 and the public internet; nothing in a private range, not
the machine itself, and not another sandbox. `--forward` lets it reach one TCP
port of the machine's own loopback as the same port on its own: a machine
whose backend is a loopback forward (`machine_address.device_api_base`) is
reached that way.

The caller is trusted with nothing but its own processes. Arguments are
checked to the character, the process must be the caller's, and the only
programs run are `ip`, `iptables-save` and `iptables-restore` from the
system's own directories. A sandbox whose processes are gone is cleaned up by
the next `up`, so one that died without its `down` leaves nothing for long.

The filter rules name no sandbox: every sandbox's link is `chs<n>` and they
all get the same treatment, so the machine's tables are written once, not per
sandbox. Changing them is slow on a busy kernel, as is creating a veth pair
(measured on a MicroCloud LXC: 0.3-2.8 s for one veth, against 25 ms for a
dummy link), so a sandbox start does only what it must.

Standard library only: this runs as root, from a copy outside every release.
"""

import ctypes
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/run/cheese-sandbox")
CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP = CGROUP_ROOT / "cheese-sandboxes"
# One /30 per sandbox: the machine's end, then the sandbox's.
NETWORK = ipaddress.ip_network("10.200.0.0/16")
SLOTS = NETWORK.num_addresses // 4
# What a sandbox may not reach. 10.0.0.0/8 holds every other sandbox too.
PRIVATE = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
# Each chain of ours, by table, and the built-in chain it goes first in.
CHAINS = {
    "filter": (("CHEESE-SBX-FWD", "FORWARD"), ("CHEESE-SBX-IN", "INPUT")),
    "nat": (("CHEESE-SBX-PRE", "PREROUTING"), ("CHEESE-SBX-POST", "POSTROUTING")),
}
NAME = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CLONE_NEWNET = 0x40000000
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class Refused(Exception):
    pass


def links(index):
    """The machine's end of a sandbox's link, and the sandbox's end."""
    return f"chs{index}", f"chp{index}"


def addresses(index):
    """The machine's address on a sandbox's link, and the sandbox's."""
    base = NETWORK.network_address + 4 * index
    return str(base + 1), str(base + 2)


# Where a sandbox's name servers are listed: the machine's own resolv.conf,
# or, when that names only a loopback stub such as systemd-resolved's, the
# upstream list the stub reads. The sandbox reads the same file
# (`bootstrap.sandbox_argv`), held to this by test_footprint_root.py.
RESOLV_CONFS = ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf")


def resolvers(text):
    """The IPv4 name servers a sandbox may ask, from resolv.conf's text. A
    loopback one is the machine's own stub, which a sandbox cannot reach."""
    found = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            try:
                address = ipaddress.ip_address(fields[1])
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback:
                found.append(str(address))
    return found


def rules(servers, ports):
    """Every rule of ours, by table and chain, for sandboxes that may ask
    `servers` for names and dial `ports` on the machine's loopback."""
    forward = [
        f"-i chs+ -d {server} -p {protocol} --dport 53 -j ACCEPT"
        for server in servers
        for protocol in ("udp", "tcp")
    ]
    forward += [f"-i chs+ -d {block} -j DROP" for block in PRIVATE]
    forward += [
        "-i chs+ -j ACCEPT",
        "-o chs+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        "-o chs+ -j DROP",
    ]
    # A forwarded port arrives addressed to the machine (`--dst-type LOCAL`)
    # and leaves for its loopback, which `route_localnet` on the link allows;
    # only a packet that was so rewritten is let in.
    local = [
        f"-i chs+ -d 127.0.0.1 -p tcp --dport {port}"
        " -m conntrack --ctstate DNAT -j ACCEPT"
        for port in ports
    ]
    local.append("-i chs+ -j DROP")
    prerouting = [
        f"-i chs+ -p tcp --dport {port} -m addrtype --dst-type LOCAL"
        f" -j DNAT --to-destination 127.0.0.1:{port}"
        for port in ports
    ]
    return {
        "filter": {"CHEESE-SBX-FWD": forward, "CHEESE-SBX-IN": local},
        "nat": {
            "CHEESE-SBX-PRE": prerouting,
            "CHEESE-SBX-POST": [f"-s {NETWORK} ! -o chs+ -j MASQUERADE"],
        },
    }


def restore(wanted, saved):
    """`iptables-restore --noflush` input that makes the tables hold `wanted`,
    or "" when `saved` (what `iptables-save` printed) shows they already do.

    Each chain is declared, which with `--noflush` empties it first, so the
    same text written twice leaves the same rules. A chain of ours not yet
    jumped to is put first in its built-in chain: Docker sets FORWARD's
    policy to DROP and ufw INPUT's, and first means neither decides for a
    sandbox. Whether the tables hold `wanted` is judged by the digest of what
    was last written, kept as a rule comment in our own chain, and the number
    of rules in each chain, so a flush by something else is noticed."""
    digest = hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest()
    marker = f'-m comment --comment "cheese-sandbox {digest[:16]}" -j RETURN'
    table, counts, present = None, {}, set()
    for line in saved.splitlines():
        if line.startswith("*"):
            table = line[1:]
        elif line.startswith("-A "):
            present.add((table, line))
            chain = line.split()[1]
            counts[chain] = counts.get(chain, 0) + 1
    jumps = {
        table: [
            f"-I {parent} 1 -j {chain}"
            for chain, parent in chains
            if (table, f"-A {parent} -j {chain}") not in present
        ]
        for table, chains in CHAINS.items()
    }
    current = ("filter", f"-A CHEESE-SBX-IN {marker}") in present and all(
        counts.get(chain, 0) == len(lines) + (chain == "CHEESE-SBX-IN")
        for chains in wanted.values()
        for chain, lines in chains.items()
    )
    if current and not any(jumps.values()):
        return ""
    text = []
    for table, chains in wanted.items():
        text.append(f"*{table}")
        text += [f":{chain} - [0:0]" for chain in chains]
        for chain, lines in chains.items():
            text += [f"-A {chain} {line}" for line in lines]
            if chain == "CHEESE-SBX-IN":
                # After the DROP: matched by nothing, there to be read back.
                text.append(f"-A {chain} {marker}")
        text += jumps[table]
        text.append("COMMIT")
    return "\n".join([*text, ""])


def inside(index, forward):
    """`ip -batch` input and `iptables-restore` input for the sandbox's own
    namespace: its link and route out, and its loopback port forwards, which
    send what it dials on 127.0.0.1 to the machine's end of its link."""
    gateway, address = addresses(index)
    _, peer = links(index)
    link = "\n".join(
        [
            f"addr add {address}/30 dev {peer}",
            "link set lo up",
            f"link set {peer} up",
            f"route add default via {gateway}",
            "",
        ]
    )
    if not forward:
        return link, ""
    nat = ["*nat"]
    nat += [
        f"-A OUTPUT -d 127.0.0.1 -p tcp --dport {port}"
        f" -j DNAT --to-destination {gateway}:{port}"
        for port in forward
    ]
    nat += [f"-A POSTROUTING -s 127.0.0.0/8 -o {peer} -j SNAT --to-source {address}"]
    return link, "\n".join([*nat, "COMMIT", ""])


def run(argv, text=""):
    result = subprocess.run(
        argv,
        input=text,
        capture_output=True,
        text=True,
        env={"PATH": SYSTEM_PATH, "LC_ALL": "C"},
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout


def sysctl(name, value):
    Path("/proc/sys", *name.split(".")).write_text(str(value))


def enter(descriptor):
    """Move this process into the network namespace open at `descriptor`."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(descriptor, CLONE_NEWNET) != 0:
        raise OSError(ctypes.get_errno(), "setns")


def caller():
    uid = os.environ.get("SUDO_UID", "")
    if os.geteuid() != 0 or not uid.isdigit() or int(uid) == 0:
        raise Refused("run through sudo by the connector user")
    return int(uid)


def owner_of(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise Refused("process has no owner")


def netns(pid):
    return os.readlink(f"/proc/{pid}/ns/net")


def alive(record):
    try:
        return netns(record["pid"]) == record["netns"]
    except OSError:
        return False


def load():
    return {path.stem: json.loads(path.read_text()) for path in STATE.glob("*.json")}


def release(name, record):
    """Take down what a sandbox's `up` made: its processes, its cgroup and its
    link."""
    group = CGROUP / name
    if group.is_dir():
        kill = group / "cgroup.kill"
        if kill.exists():
            kill.write_text("1")
        else:
            for pid in (group / "cgroup.procs").read_text().split():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        while group.exists():
            try:
                group.rmdir()
            except FileNotFoundError:
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
    host, _ = links(record["index"])
    if Path("/sys/class/net", host).exists():
        subprocess.run(
            ["ip", "link", "del", host], capture_output=True, env={"PATH": SYSTEM_PATH}
        )
    (STATE / f"{name}.json").unlink(missing_ok=True)


def write_rules(records):
    servers = []
    for path in RESOLV_CONFS:
        if not servers and Path(path).is_file():
            servers = resolvers(Path(path).read_text())
    if not servers:
        raise Refused("no name server a sandbox can reach: " + ", ".join(RESOLV_CONFS))
    ports = sorted({port for record in records.values() for port in record["forward"]})
    saved = run(["iptables-save", "-t", "filter"]) + run(["iptables-save", "-t", "nat"])
    text = restore(rules(servers, ports), saved)
    if text:
        run(["iptables-restore", "--noflush"], text)


def enable(directory):
    """Hand the cpu, memory and pids controllers to `directory`'s children."""
    control = directory / "cgroup.subtree_control"
    missing = {"cpu", "memory", "pids"} - set(control.read_text().split())
    if missing:
        control.write_text(" ".join("+" + name for name in sorted(missing)))


def limit(name, pid, limits):
    enable(CGROUP_ROOT)
    CGROUP.mkdir(exist_ok=True)
    enable(CGROUP)
    group = CGROUP / name
    group.mkdir()
    (group / "memory.max").write_text(str(limits["memory_mb"] * 1024 * 1024))
    swap = group / "memory.swap.max"
    if swap.exists():
        swap.write_text(str(limits["swap_mb"] * 1024 * 1024))
    period = 100000
    (group / "cpu.max").write_text(f"{limits['cpus'] * period} {period}")
    (group / "pids.max").write_text(str(limits["pids"]))
    (group / "cgroup.procs").write_text(str(pid))


def connect(index, pid, forward):
    """The sandbox's link: made with its far end already in the sandbox,
    which costs one slow kernel operation where creating it here and moving
    it costs two."""
    host, peer = links(index)
    gateway, _ = addresses(index)
    run(["ip", "link", "add", host, "type", "veth", "peer", peer, "netns", str(pid)])
    sysctl(f"net.ipv6.conf.{host}.disable_ipv6", 1)
    # Strict reverse-path filtering: a packet from the link must carry the
    # sandbox's own address, so no rule has to name it.
    sysctl(f"net.ipv4.conf.{host}.rp_filter", 1)
    if forward:
        sysctl(f"net.ipv4.conf.{host}.route_localnet", 1)
    sysctl("net.ipv4.ip_forward", 1)
    run(
        ["ip", "-batch", "-"], f"addr add {gateway}/30 dev {host}\nlink set {host} up\n"
    )
    machine = os.open("/proc/self/ns/net", os.O_RDONLY)
    sandbox = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    try:
        enter(sandbox)
        # No IPv6 at all: a link-local address would reach the machine's
        # services over the link, which no IPv4 rule would see.
        sysctl("net.ipv6.conf.all.disable_ipv6", 1)
        sysctl("net.ipv6.conf.default.disable_ipv6", 1)
        link, nat = inside(index, forward)
        run(["ip", "-batch", "-"], link)
        if nat:
            sysctl(f"net.ipv4.conf.{peer}.route_localnet", 1)
            run(["iptables-restore", "--noflush"], nat)
    finally:
        enter(machine)
        os.close(machine)
        os.close(sandbox)


def up(name, pid, limits, forward):
    uid = caller()
    if owner_of(pid) != uid:
        raise Refused("process is not the caller's")
    namespace = netns(pid)
    if namespace == netns(os.getpid()):
        raise Refused("process has no network namespace of its own")
    STATE.mkdir(mode=0o700, exist_ok=True)
    with open(STATE / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = load()
        for other, record in list(records.items()):
            if other == name or not alive(record):
                release(other, record)
                del records[other]
            elif record["netns"] == namespace:
                raise Refused("namespace already belongs to another sandbox")
        used = {record["index"] for record in records.values()}
        index = next(slot for slot in range(SLOTS) if slot not in used)
        record = {
            "index": index,
            "pid": pid,
            "netns": namespace,
            "uid": uid,
            "forward": forward,
        }
        (STATE / f"{name}.json").write_text(json.dumps(record))
        try:
            records[name] = record
            write_rules(records)
            connect(index, pid, forward)
            limit(name, pid, limits)
        except BaseException:
            release(name, record)
            raise
    gateway, address = addresses(index)
    return {"address": address, "gateway": gateway, "link": links(index)[0]}


def down(name):
    uid = caller()
    if not STATE.is_dir():
        return {}
    with open(STATE / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = load().get(name)
        if record is None:
            return {}
        if record["uid"] != uid:
            raise Refused("sandbox is another user's")
        release(name, record)
    return {}


def number(text, low, high):
    if not re.fullmatch(r"[0-9]{1,9}", text) or not low <= int(text) <= high:
        raise Refused(f"{text!r} is not a number from {low} to {high}")
    return int(text)


LIMITS = {
    "--memory-mb": ("memory_mb", 64, 1 << 20),
    "--swap-mb": ("swap_mb", 0, 1 << 20),
    "--cpus": ("cpus", 1, 1024),
    "--pids": ("pids", 16, 1 << 22),
}


def parse(argv):
    """The command line, checked to the character: (command, arguments)."""
    if len(argv) == 2 and argv[0] == "down" and NAME.fullmatch(argv[1]):
        return "down", {"name": argv[1]}
    if len(argv) < 3 or argv[0] != "up" or not NAME.fullmatch(argv[1]):
        raise Refused("usage: up NAME PID --memory-mb N ... | down NAME")
    pid = number(argv[2], 2, 1 << 22)
    limits, forward = {}, []
    options = argv[3:]
    if len(options) % 2:
        raise Refused("every option takes a value")
    for option, value in zip(options[::2], options[1::2], strict=True):
        if option == "--forward" and len(forward) < 4:
            port = number(value, 1, 65535)
            if port not in forward:
                forward.append(port)
        elif option in LIMITS and LIMITS[option][0] not in limits:
            key, low, high = LIMITS[option]
            limits[key] = number(value, low, high)
        else:
            raise Refused(f"unexpected option {option}")
    if len(limits) != len(LIMITS):
        raise Refused("every limit is required")
    return "up", {"name": argv[1], "pid": pid, "limits": limits, "forward": forward}


def main(argv):
    started = time.monotonic()
    try:
        command, arguments = parse(argv)
        answer: dict = (up if command == "up" else down)(**arguments)
    except Refused as refusal:
        print(f"cheese-sandbox: {refusal}", file=sys.stderr)
        return 2
    answer["ms"] = round((time.monotonic() - started) * 1000)
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
