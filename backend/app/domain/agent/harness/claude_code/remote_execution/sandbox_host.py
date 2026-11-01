#!/usr/bin/python3 -I
"""Give one session's sandbox its own network and resource limits, as root.

Installed on a Cloud machine as `bootstrap.SANDBOX_HOST` and run by the
machine's connector user through `sudo -n`, once when a sandbox starts and
once when it goes. Everything else about a sandbox is unprivileged
bubblewrap; these things are not: a network namespace that reaches the
internet needs a veth pair, NAT and filter rules in the machine's own tables,
a memory or CPU limit needs a cgroup only root can create, and a disk limit
needs a project quota only root can set.

    up NAME PID --home PATH --memory-mb N --swap-mb N --cpus N --pids N
             --disk-mib N --inodes N [--forward PORT]... [--site PORT]
    down NAME
    reclaim NAME...
    swap NAME MIB
    freeze NAME...
    thaw NAME...

`PID` is the sandbox's first process, created by bubblewrap with a network
namespace of its own and still waiting to start the session's program. `up`
creates a veth pair with one end in that namespace and puts the process in a
cgroup with the given limits.

`PATH` is the sandbox's home, which must be on an XFS filesystem mounted with
project quotas (a platform host's data disk, `/data`, set up when the host
enrolls: `enrollment.bootstrap_script`); a sandbox whose home is anywhere else
is refused, never run without a disk limit. `up` gives the home a project of
its own, inherited by everything made in it, limited to `--disk-mib` and
`--inodes`; the executor's state inside it (`STATE_DIR`) a second, small one,
so a sandbox that filled its home can still report that it did; and its
project's package store, shared by the project's sandboxes on this machine,
a third with the same limits as a home. Inside its sandbox a session cannot
change a file's project or take the inheritance off a directory: the kernel
allows that only from the machine's own user namespace. `down` lifts the
home's and the state's limits; the store keeps its own. `reclaim` asks the
kernel to give back the page cache of sandboxes the platform found idle
(`disk_guard`), which on a VM host is memory the hypervisor can then take
back from the machine. `swap` sets how much of the host's swap file one
sandbox may use (`memory.swap.max`, 0 by default: a sandbox at its memory
limit is killed, not pushed into swap). `freeze` and `thaw` stop and resume
every process in a sandbox with `cgroup.freeze`, waiting until the kernel
reports the change in `cgroup.events`: a frozen sandbox keeps its memory
reserved but spends no CPU, and its pages can then be swapped out. None of
these three is called by the lifecycle yet; they are the host side the
freezing step needs. The sandbox may reach the machine's DNS
resolvers on port 53 and the public internet; nothing in a private range, not
the machine itself, and not another sandbox. `--forward` lets it reach one TCP
port of the machine's own loopback as the same port on its own: a machine
whose backend is a loopback forward (`machine_address.device_api_base`) is
reached that way. `--site` lets it reach one more, as port 443 of
`SITE_ADDRESS` on its own: the machine's forward of the deployment's site
over TLS, which the sandbox's /etc/hosts gives the site's name
(`bootstrap.start_sandbox`, `confinement.site_hosts`).

The caller is trusted with nothing but its own processes. Arguments are
checked to the character, the process must be the caller's, and the only
programs run are root's copies of the platform's own static `ip`, iptables
(its nf_tables build, as every current distribution's is) and `xfs_quota`, in
`TOOLS` (`bootstrap.sandbox_tools`), never whatever the system has. A sandbox whose processes are gone is cleaned up by
the next `up`, so one that died without its `down` leaves nothing for long.

The filter rules name no sandbox: every sandbox's link is `chs<n>` and they
all get the same treatment, so the machine's tables are written once, not per
sandbox. Changing them is slow on a busy kernel, as is creating a veth pair
(measured on a MicroCloud LXC: 0.3-2.8 s for one veth, against 25 ms for a
dummy link), so a sandbox start does only what it must.

Standard library only: this runs as root, from a copy outside every release.
"""

import ctypes
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/run/cheese-sandbox")
# Which project quota each home, executor state and store has, by name. On the
# machine's own disk, not in /run: the projects are on files that outlive a
# reboot, and a home started again after one keeps its project.
PROJECTS = Path("/var/lib/cheese-sandbox/projects.json")
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
# A copy of `bootstrap.SANDBOX_TOOLS`, held to it by test_footprint_root.py.
TOOLS = "/usr/local/libexec/cheese-sandbox-tools"
IP = f"{TOOLS}/ip"
XFS_QUOTA = f"{TOOLS}/xfs_quota"
# One binary for every iptables command, which it takes as its first argument.
IPTABLES_SAVE = (f"{TOOLS}/xtables-nft-multi", "iptables-save")
IPTABLES_RESTORE = (f"{TOOLS}/xtables-nft-multi", "iptables-restore", "--noflush")
# Where a sandbox dials the site, inside its own loopback range and apart
# from 127.0.0.1, so a server of the room's own on 127.0.0.1:443 is left
# alone. A copy is in `confinement.SITE_ADDRESS`, held to this by
# test_footprint_root.py.
SITE_ADDRESS = "127.0.0.2"


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
# (`confinement.sandbox_argv`), held to this by test_footprint_root.py.
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
    same text written twice leaves the same rules. The jump to each chain of
    ours is the first rule of its built-in chain: Docker sets FORWARD's
    policy to DROP and ufw INPUT's, and first means neither decides for a
    sandbox. A jump something else has since put a rule ahead of is moved
    back to the top: Docker inserts its own at the top of FORWARD and
    POSTROUTING each time its daemon starts, and a sandbox behind them could
    reach a container's published port and be reached from it. Whether the tables hold `wanted` is judged by the digest of what
    was last written, kept as a rule comment in our own chain, and the number
    of rules in each chain, so a flush by something else is noticed."""
    digest = hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest()
    marker = f'-m comment --comment "cheese-sandbox {digest[:16]}" -j RETURN'
    table, counts, present, first = None, {}, {}, {}
    for line in saved.splitlines():
        if line.startswith("*"):
            table = line[1:]
        elif line.startswith("-A "):
            present[table, line] = present.get((table, line), 0) + 1
            chain = line.split()[1]
            counts[chain] = counts.get(chain, 0) + 1
            first.setdefault((table, chain), line)
    jumps = {
        table: [
            edit
            for chain, parent in chains
            if first.get((table, parent)) != f"-A {parent} -j {chain}"
            for edit in [
                *[f"-D {parent} -j {chain}"]
                * present.get((table, f"-A {parent} -j {chain}"), 0),
                f"-I {parent} 1 -j {chain}",
            ]
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


def inside(index, forward, site=None):
    """`ip -batch` input and `iptables-restore` input for the sandbox's own
    namespace: its link and route out, and its loopback port forwards, which
    send what it dials on 127.0.0.1 to the machine's end of its link, and
    what it dials on `SITE_ADDRESS`:443 to the machine's `site` port."""
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
    if not forward and not site:
        return link, ""
    nat = ["*nat"]
    nat += [
        f"-A OUTPUT -d 127.0.0.1 -p tcp --dport {port}"
        f" -j DNAT --to-destination {gateway}:{port}"
        for port in forward
    ]
    if site:
        nat.append(
            f"-A OUTPUT -d {SITE_ADDRESS} -p tcp --dport 443"
            f" -j DNAT --to-destination {gateway}:{site}"
        )
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
        # Hand-written, not tenacity: this runs on machines with the stdlib only.
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
            [IP, "link", "del", host], capture_output=True, env={"PATH": SYSTEM_PATH}
        )
    (STATE / f"{name}.json").unlink(missing_ok=True)


def write_rules(records):
    servers = []
    for path in RESOLV_CONFS:
        if not servers and Path(path).is_file():
            servers = resolvers(Path(path).read_text())
    if not servers:
        raise Refused("no name server a sandbox can reach: " + ", ".join(RESOLV_CONFS))
    ports = sorted(
        {
            port
            for record in records.values()
            for port in [*record["forward"], *filter(None, [record.get("site")])]
        }
    )
    saved = run([*IPTABLES_SAVE, "-t", "filter"]) + run([*IPTABLES_SAVE, "-t", "nat"])
    text = restore(rules(servers, ports), saved)
    if text:
        run([*IPTABLES_RESTORE], text)


def enable(directory):
    """Hand the cpu, memory and pids controllers to `directory`'s children."""
    control = directory / "cgroup.subtree_control"
    missing = {"cpu", "memory", "pids"} - set(control.read_text().split())
    if missing:
        control.write_text(" ".join("+" + name for name in sorted(missing)))


def machine_memory():
    """The machine's memory in bytes: /proc/meminfo's total, which in a
    MicroCloud LXC is the container's own limit (lxcfs)."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("no MemTotal in /proc/meminfo")


def sandboxes_memory(total):
    """What every sandbox on a machine of `total` bytes may hold together:
    the machine keeps a quarter, at least 1 GiB, for everything outside them.

    Each sandbox's own limit does not add up to this: two 3 GiB sandboxes on a
    4 GiB machine together drove it to its container limit (a dev pool host,
    2026-10-05), and the reclaim that followed stalled every process in the
    container, sshd, PID 1, dbus and logind with them, for five hours. Held
    here, the kernel reclaims and kills inside the sandboxes instead."""
    return total - min(max(1 << 30, total // 4), total // 2)


def limit(name, pid, limits):
    enable(CGROUP_ROOT)
    CGROUP.mkdir(exist_ok=True)
    (CGROUP / "memory.max").write_text(str(sandboxes_memory(machine_memory())))
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


def connect(index, pid, forward, site=None):
    """The sandbox's link: made with its far end already in the sandbox,
    which costs one slow kernel operation where creating it here and moving
    it costs two."""
    host, peer = links(index)
    gateway, _ = addresses(index)
    run([IP, "link", "add", host, "type", "veth", "peer", peer, "netns", str(pid)])
    sysctl(f"net.ipv6.conf.{host}.disable_ipv6", 1)
    # Strict reverse-path filtering: a packet from the link must carry the
    # sandbox's own address, so no rule has to name it.
    sysctl(f"net.ipv4.conf.{host}.rp_filter", 1)
    if forward or site:
        sysctl(f"net.ipv4.conf.{host}.route_localnet", 1)
    sysctl("net.ipv4.ip_forward", 1)
    run([IP, "-batch", "-"], f"addr add {gateway}/30 dev {host}\nlink set {host} up\n")
    machine = os.open("/proc/self/ns/net", os.O_RDONLY)
    sandbox = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    try:
        enter(sandbox)
        # No IPv6 at all: a link-local address would reach the machine's
        # services over the link, which no IPv4 rule would see.
        sysctl("net.ipv6.conf.all.disable_ipv6", 1)
        sysctl("net.ipv6.conf.default.disable_ipv6", 1)
        link, nat = inside(index, forward, site)
        run([IP, "-batch", "-"], link)
        if nat:
            sysctl(f"net.ipv4.conf.{peer}.route_localnet", 1)
            run([*IPTABLES_RESTORE], nat)
    finally:
        enter(machine)
        os.close(machine)
        os.close(sandbox)


# The executor's state inside a sandbox's home, which has a project and a
# limit of its own: a session that fills its home leaves its executor room
# to answer, and to say the disk is full. A copy of `bootstrap.PLATFORM_DIR`
# and its `executor`, held to it by test_footprint_root.py.
STATE_DIR = ".cheese/executor"
# What the state may hold: the executor's journal and every command's output
# (`runtime_commands`), which a build log can make large, in a share of a
# home's limit.
STATE_DISK_MIB = 1024
STATE_INODES = 50_000
FS_IOC_FSGETXATTR = 0x801C581F
FS_IOC_FSSETXATTR = 0x401C5820
FS_XFLAG_PROJINHERIT = 0x200
# struct fsxattr: xflags, extsize, nextents, projid, cowextsize, pad[8].
FSXATTR = struct.Struct("IIIII8s")


def open_directory(path, uid):
    """`path`, an absolute directory of `uid`'s, opened without following a
    link anywhere along it: a session writes its own home, and a link it
    left there must not have root tag or limit anything else."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        if os.fstat(descriptor).st_uid != uid:
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused(f"{path} is not the caller's")
    except OSError as error:
        os.close(descriptor)
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused(f"{path} is not a directory: {error.strerror}") from None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def open_beneath(descriptor, relative, uid):
    """`relative` under the directory open at `descriptor`, opened as
    `open_directory` opens a path, or None where it is not there."""
    current = os.dup(descriptor)
    try:
        for part in relative.split("/"):
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = following
        if os.fstat(current).st_uid != uid:
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused(f"{relative} is not the caller's")
    except FileNotFoundError:
        os.close(current)
        return None
    except OSError as error:
        os.close(current)
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused(f"{relative} is not a directory: {error.strerror}") from None
    except BaseException:
        os.close(current)
        raise
    return current


def quota_mount(descriptor):
    """Where the filesystem holding `descriptor` is mounted, when it is XFS
    with project quotas enforced; refused otherwise."""
    device = os.fstat(descriptor).st_dev
    wanted = f"{os.major(device)}:{os.minor(device)}"
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, _, after = line.partition(" - ")
        fields, rest = before.split(), after.split()
        if (
            fields[2] == wanted
            and fields[3] == "/"
            and rest[0] == "xfs"
            and {"prjquota", "pquota"} & set(rest[2].split(","))
        ):
            return fields[4].replace("\\040", " ")
    # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
    raise Refused("the sandbox's home is not on a disk with project quotas")


def project_of(descriptor):
    """A file's project and whether it is inherited by what is made in it."""
    fields = FSXATTR.unpack(fcntl.ioctl(descriptor, FS_IOC_FSGETXATTR, bytes(28)))
    return fields[3], bool(fields[0] & FS_XFLAG_PROJINHERIT)


def set_project(descriptor, project, directory):
    fields = list(FSXATTR.unpack(fcntl.ioctl(descriptor, FS_IOC_FSGETXATTR, bytes(28))))
    fields[3] = project
    if directory:
        fields[0] |= FS_XFLAG_PROJINHERIT
    fcntl.ioctl(descriptor, FS_IOC_FSSETXATTR, FSXATTR.pack(*fields))


def tag(descriptor, project):
    """Put the tree open at `descriptor` in `project`, the directory itself
    last, so a directory that has it was tagged whole. A tree already tagged
    is left alone: whatever is made in it inherits."""
    if project_of(descriptor) == (project, True):
        return
    for _, _, files, directory in os.fwalk(
        ".", dir_fd=descriptor, topdown=False, follow_symlinks=False
    ):
        for name in files:
            try:
                file = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
                    dir_fd=directory,
                )
            except OSError:
                # A link or a socket: nothing to charge.
                continue
            try:
                set_project(file, project, directory=False)
            finally:
                os.close(file)
        set_project(directory, project, directory=True)


def limit_project(mount, project, disk_mib, inodes):
    """Set a project's limits; 0 for both lifts them."""
    run(
        [
            XFS_QUOTA,
            "-x",
            "-c",
            f"limit -p bhard={disk_mib}m ihard={inodes} {project}",
            mount,
        ]
    )


def projects():
    try:
        return json.loads(PROJECTS.read_text())
    except FileNotFoundError:
        return {}


def keep_projects(table):
    PROJECTS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = PROJECTS.with_suffix(".next")
    temporary.write_text(json.dumps(table, sort_keys=True))
    temporary.replace(PROJECTS)


def project_for(table, key, uid, mount, path):
    """The project `key` has on the filesystem at `mount`, or the lowest one
    nothing has there."""
    if table.get(key, {}).get("mount") != mount:
        used = {entry["id"] for entry in table.values() if entry["mount"] == mount}
        table[key] = {
            "id": next(n for n in range(1, 1 << 31) if n not in used),
            "uid": uid,
            "mount": mount,
            "path": path,
        }
    return table[key]["id"]


def forget_gone(table):
    """Forget the projects whose directory is gone, lifting their limits: a
    home deleted without its `down`, a store of a machine's project that
    ended. Whatever they held went with the directory."""
    for key, entry in list(table.items()):
        if not os.path.isdir(entry["path"]):
            if os.path.isdir(entry["mount"]):
                limit_project(entry["mount"], entry["id"], 0, 0)
            del table[key]


def disk(name, uid, home, limits):
    """Give a sandbox's home, its executor's state and its project's store
    each a project quota (see the module's text)."""
    home_fd = open_directory(home, uid)
    opened = [home_fd]
    try:
        mount = quota_mount(home_fd)
        project = Path(home).parent.name
        store = str(Path(home).parents[2] / "store" / project)
        store_fd = open_directory(store, uid)
        opened.append(store_fd)
        table = projects()
        forget_gone(table)
        ids = {
            kind: project_for(table, key, uid, mount, path)
            for kind, key, path in (
                ("home", "home:" + name, home),
                ("state", "state:" + name, str(Path(home) / STATE_DIR)),
                ("store", "store:" + project, store),
            )
        }
        keep_projects(table)
        tag(home_fd, ids["home"])
        state_fd = open_beneath(home_fd, STATE_DIR, uid)
        if state_fd is not None:
            opened.append(state_fd)
            tag(state_fd, ids["state"])
        tag(store_fd, ids["store"])
        limit_project(mount, ids["home"], limits["disk_mib"], limits["inodes"])
        limit_project(mount, ids["state"], STATE_DISK_MIB, STATE_INODES)
        limit_project(mount, ids["store"], limits["disk_mib"], limits["inodes"])
    finally:
        for descriptor in opened:
            os.close(descriptor)


def free_disk(name, uid):
    """Lift the limits of a sandbox's home and state; its home is deleted
    next. Their projects stay taken until it is (`forget_gone`): a project
    handed to another sandbox while the files are still there would count
    them against it. The store keeps its own."""
    table = projects()
    for key in ("home:" + name, "state:" + name):
        entry = table.get(key)
        if entry is None:
            continue
        if entry["uid"] != uid:
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused("sandbox is another user's")
        limit_project(entry["mount"], entry["id"], 0, 0)


def up(name, pid, home, limits, forward, site=None):
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
            "site": site,
        }
        (STATE / f"{name}.json").write_text(json.dumps(record))
        try:
            records[name] = record
            disk(name, uid, home, limits)
            write_rules(records)
            connect(index, pid, forward, site)
            limit(name, pid, limits)
        except BaseException:
            release(name, record)
            raise
    gateway, address = addresses(index)
    return {"address": address, "gateway": gateway, "link": links(index)[0]}


def down(name):
    uid = caller()
    STATE.mkdir(mode=0o700, exist_ok=True)
    with open(STATE / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = load().get(name)
        if record is not None:
            if record["uid"] != uid:
                raise Refused("sandbox is another user's")
            release(name, record)
        # Whether or not its processes are still recorded: a machine that
        # rebooted has lost /run, and not the limits on its disk.
        free_disk(name, uid)
    return {}


def reclaim(names):
    """Have each named sandbox of the caller's give back what of its memory
    the kernel can take without killing anything: its page cache, since a
    sandbox has no swap. Answers how much each gave back."""
    uid = caller()
    given = {}
    if not STATE.is_dir():
        return {"reclaimed": given}
    records = load()
    for name in names:
        record = records.get(name)
        group = CGROUP / name
        if record is None or record["uid"] != uid or not group.is_dir():
            continue
        before = int((group / "memory.current").read_text())
        try:
            (group / "memory.reclaim").write_text(str(before))
        except OSError as error:
            # EAGAIN: it could not give back all that was asked, which with
            # anonymous memory in it is always so; what it did give is kept.
            if error.errno != errno.EAGAIN:
                raise
        given[name] = max(0, before - int((group / "memory.current").read_text()))
    return {"reclaimed": given}


#: How long `freeze` and `thaw` wait for the kernel to report the change in
#: `cgroup.events`. It applies the change before its processes have stopped.
FREEZE_TIMEOUT_S = 5.0


def _own_groups(names):
    """The cgroups of the named sandboxes, which must be the caller's own and
    have been made by ``up``: one check for every name before any is touched,
    so a group of names either all moves or none does."""
    uid = caller()
    records = load() if STATE.is_dir() else {}
    groups = []
    for name in names:
        record = records.get(name)
        group = CGROUP / name
        if record is None or record["uid"] != uid or not group.is_dir():
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused(f"{name} is not the caller's sandbox")
        groups.append(group)
    return groups


def swap(name, mib):
    """How much of the host's swap file one sandbox may use, in MiB, written
    to its `memory.swap.max`. 0 keeps it out of swap; `up` writes this at
    start, and this is how a later step changes it."""
    (group,) = _own_groups([name])
    try:
        (group / "memory.swap.max").write_text(str(mib * 1024 * 1024))
    except OSError as error:
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused(f"could not set {name}'s swap: {error.strerror}") from error
    return {"swap_max": mib * 1024 * 1024}


def _frozen(group):
    """Whether the kernel says every process in `group` is frozen."""
    for line in (group / "cgroup.events").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "frozen":
            return parts[1] == "1"
    return None


def _set_frozen(names, frozen):
    """Freeze or thaw every process in the named sandboxes and wait until the
    kernel reports it in each one's `cgroup.events`; a change it does not
    report within ``FREEZE_TIMEOUT_S`` is refused."""
    groups = _own_groups(names)
    for group in groups:
        try:
            (group / "cgroup.freeze").write_text("1" if frozen else "0")
        except OSError as error:
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused(f"could not freeze {group.name}: {error.strerror}") from error
    deadline = time.monotonic() + FREEZE_TIMEOUT_S
    for group in groups:
        try:
            while _frozen(group) != frozen:
                if time.monotonic() >= deadline:
                    # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
                    raise Refused(f"{group.name} did not change state in time")
                time.sleep(0.01)
        except OSError as error:
            # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
            raise Refused(
                f"could not read {group.name}'s state: {error.strerror}"
            ) from error
    return {"frozen" if frozen else "thawed": [group.name for group in groups]}


def freeze(names):
    return _set_frozen(names, True)


def thaw(names):
    return _set_frozen(names, False)


def number(text, low, high):
    if not re.fullmatch(r"[0-9]{1,9}", text) or not low <= int(text) <= high:
        raise Refused(f"{text!r} is not a number from {low} to {high}")
    return int(text)


LIMITS = {
    "--memory-mb": ("memory_mb", 64, 1 << 20),
    "--swap-mb": ("swap_mb", 0, 1 << 20),
    "--cpus": ("cpus", 1, 1024),
    "--pids": ("pids", 16, 1 << 22),
    "--disk-mib": ("disk_mib", 64, 1 << 30),
    "--inodes": ("inodes", 1000, 1 << 31),
}
# A home's absolute path: no `..`, no empty part, nothing a shell or the
# quota tool's command line would read as more than one word.
HOME_PATH = re.compile(r"(/[A-Za-z0-9._-]+)+")


def home_path(text, name):
    """A sandbox's home as `up` takes it: absolute, plain, and named after
    the sandbox, inside a directory named after its project."""
    parts = text.split("/")
    if (
        not HOME_PATH.fullmatch(text)
        or any(part in {".", ".."} for part in parts)
        or parts[-1] != name
        or not NAME.fullmatch(parts[-2])
    ):
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused(f"{text!r} is not a sandbox's home")
    return text


def parse(argv):
    """The command line, checked to the character: (command, arguments)."""
    if len(argv) == 2 and argv[0] == "down" and NAME.fullmatch(argv[1]):
        return "down", {"name": argv[1]}
    if (
        2 <= len(argv) <= 65
        and argv[0] in ("reclaim", "freeze", "thaw")
        and all(NAME.fullmatch(name) for name in argv[1:])
    ):
        return argv[0], {"names": argv[1:]}
    if len(argv) == 3 and argv[0] == "swap" and NAME.fullmatch(argv[1]):
        return "swap", {"name": argv[1], "mib": number(argv[2], 0, 1 << 20)}
    if len(argv) < 3 or argv[0] != "up" or not NAME.fullmatch(argv[1]):
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused(
            "usage: up NAME PID --home PATH ... | down NAME | reclaim NAME..."
            " | swap NAME MIB | freeze NAME... | thaw NAME..."
        )
    pid = number(argv[2], 2, 1 << 22)
    limits, forward, site, home = {}, [], None, None
    options = argv[3:]
    if len(options) % 2:
        raise Refused("every option takes a value")
    for option, value in zip(options[::2], options[1::2], strict=True):
        if option == "--home" and home is None:
            home = home_path(value, argv[1])
        elif option == "--forward" and len(forward) < 4:
            port = number(value, 1, 65535)
            if port not in forward:
                forward.append(port)
        elif option == "--site" and site is None:
            site = number(value, 1, 65535)
        elif option in LIMITS and LIMITS[option][0] not in limits:
            key, low, high = LIMITS[option]
            limits[key] = number(value, low, high)
        else:
            raise Refused(f"unexpected option {option}")
    if len(limits) != len(LIMITS) or home is None:
        # i18n-exempt: root helper on the machine, stdlib only; its words go to the install's log
        raise Refused("the home and every limit are required")
    return "up", {
        "name": argv[1],
        "pid": pid,
        "home": home,
        "limits": limits,
        "forward": forward,
        "site": site,
    }


def main(argv):
    started = time.monotonic()
    try:
        command, arguments = parse(argv)
        answer: dict = {
            "up": up,
            "down": down,
            "reclaim": reclaim,
            "swap": swap,
            "freeze": freeze,
            "thaw": thaw,
        }[command](**arguments)
    except Refused as refusal:
        print(f"cheese-sandbox: {refusal}", file=sys.stderr)
        return 2
    answer["ms"] = round((time.monotonic() - started) * 1000)
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
