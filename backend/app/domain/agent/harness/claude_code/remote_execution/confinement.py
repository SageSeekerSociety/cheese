"""What a session's sandbox may not do, shipped in the release and loaded
by the bootstrap when it starts one (`bootstrap.start_sandbox`): on Linux the
seccomp program bubblewrap applies, on macOS the Seatbelt profile
`sandbox-exec` applies.

Runs on the machine with the Python the executor runs on, as old as 3.9.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Where a sandbox dials the deployment's site when its machine forwards it
# (`site_hosts`). A copy of `sandbox_host.SITE_ADDRESS`, held to it by
# test_footprint_root.py.
SITE_ADDRESS = "127.0.0.2"


def site_hosts(env, fds, machine="/etc/hosts"):
    """When the backend says the machine forwards the deployment's site
    (`CHEESE_SITE_FORWARD`, `machine_address.site_forward`), the /etc/hosts a
    sandbox reads: the machine's, with the site's name at `SITE_ADDRESS`,
    whose port 443 the sandbox helper passes to the forward. It goes to
    bubblewrap on a pipe, `fds["hosts"]`, so nothing in the room can change
    it. Answers the forward's port, or None when there is none. The machine's
    resolver answers the public address, which leaves the private network
    for the public relay and comes back through its tunnels."""
    host, _, port = (env.get("CHEESE_SITE_FORWARD") or "").rpartition(":")
    if not host or not port.isdigit():
        return None
    text = Path(machine).read_text().rstrip("\n") + f"\n{SITE_ADDRESS} {host}\n"
    fds["hosts"], write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    return int(port)


# The syscalls a sandbox is refused (`seccomp_filter`), by architecture:
# (AUDIT_ARCH, {name: number}, clone, unshare).
SYSCALLS = {
    "x86_64": (
        0xC000003E,
        {
            "ptrace": 101,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "add_key": 248,
            "request_key": 249,
            "keyctl": 250,
            "io_uring_setup": 425,
            "io_uring_enter": 426,
            "io_uring_register": 427,
        },
        56,
        272,
    ),
    "aarch64": (
        0xC00000B7,
        {
            "ptrace": 117,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "add_key": 217,
            "request_key": 218,
            "keyctl": 219,
            "io_uring_setup": 425,
            "io_uring_enter": 426,
            "io_uring_register": 427,
        },
        220,
        97,
    ),
}
CLONE3 = 435
CLONE_NEWUSER = 0x10000000


def seccomp_filter(machine=None):
    """The seccomp program a sandbox runs under, as bubblewrap's `--seccomp`
    reads it: classic BPF over `struct seccomp_data`.

    It refuses what lets one process read or steer another, or that has been
    the way into the kernel more often than it is needed for development
    work: `ptrace` and `process_vm_readv`/`writev`; io_uring, refused as
    absent so a runtime falls back to ordinary I/O as it does on an old
    kernel; the kernel keyring; and a further user namespace, which is what
    makes most of the kernel's privileged interfaces reachable to an
    unprivileged process. The namespace one is refused by its flag on
    `clone` and `unshare`, and `clone3`, whose flags this cannot read, is
    refused as absent so the C library falls back to `clone`. bubblewrap's
    own `--disable-userns` does this with a sysctl an LXC container cannot
    write. Any other architecture's calls, x32's and i386's on an x86_64
    machine, end the process: the numbers above are only the native ones'."""
    import struct

    arch, denied, clone, unshare = SYSCALLS[machine or os.uname().machine]
    load, equal, above, test, ret = 0x20, 0x15, 0x35, 0x45, 0x06
    allow, kill = 0x7FFF0000, 0x80000000
    eperm, enosys = 0x00050000 | 1, 0x00050000 | 38
    # (code, jump-if-true label, jump-if-false label, k); labels resolve below.
    program = [
        (None, load, None, None, 4),
        (None, equal, "native", "kill", arch),
        ("native", load, None, None, 0),
    ]
    if arch == SYSCALLS["x86_64"][0]:
        program.append((None, above, "eperm", None, 0x40000000))
    for number in sorted(denied.values()):
        target = "enosys" if number in (425, 426, 427) else "eperm"
        program.append((None, equal, target, None, number))
    program += [
        (None, equal, "enosys", None, CLONE3),
        (None, equal, "flags", None, clone),
        (None, equal, "flags", None, unshare),
        (None, ret, None, None, allow),
        ("flags", load, None, None, 16),
        (None, test, "eperm", None, CLONE_NEWUSER),
        (None, ret, None, None, allow),
        ("eperm", ret, None, None, eperm),
        ("enosys", ret, None, None, enosys),
        ("kill", ret, None, None, kill),
    ]
    labels = {entry[0]: index for index, entry in enumerate(program) if entry[0]}
    code = b""
    for index, (_, operation, true, false, k) in enumerate(program):
        jumps = [labels[name] - index - 1 if name else 0 for name in (true, false)]
        code += struct.pack("<HBBI", operation, *jumps, k)
    return code


def sbpl(path):
    """`path` as a Seatbelt string: resolved, because the kernel matches the
    real path (`/tmp` is `/private/tmp`), and quoted as JSON quotes it, which
    the profile language reads the same way."""
    return json.dumps(os.path.realpath(str(path)))


def seatbelt_profile(*, owner, writable, readable, link):
    """The macOS sandbox for a room, as `bootstrap.sandbox_argv` builds the
    Linux one: the machine readable and nothing of it writable, and of the
    owner's home only what is named.

    `writable` (the room's home, its project's package store, the directory
    holding the executor's sockets) is writable and readable, and so is
    `link`, the name the executor links beside that directory for the
    connector to dial. `readable` (the toolchain, the release store, the
    Claude build) is readable too, and so is a Python kept in the home.
    Everything else in the owner's home is unreadable (the connector's
    credential is under `~/Library/Application Support`), and so are mounted
    volumes. macOS has no pid namespace, so what stands in for one is here as
    well: a signal reaches only processes of this sandbox, a Unix socket is
    dialled only in the room's own directories (another program of the
    owner's listening on one, a terminal multiplexer among them, runs
    outside), and nothing is handed to LaunchServices or the pasteboard, both
    of which act outside the sandbox on its behalf."""
    readable = [*writable, *readable]
    prefixes = {Path(sys.base_prefix), Path(sys.prefix)}
    for prefix in sorted(prefixes | {path.resolve() for path in prefixes}):
        if owner in prefix.parents:
            readable.append(prefix)
    # Reaching a path takes a look at each directory above it, which the
    # rule on the owner's home would otherwise refuse.
    above = sorted(
        {
            str(parent)
            for path in readable
            for parent in Path(os.path.realpath(str(path))).parents
        }
    )
    # The link itself, not where it leads: resolved above it only.
    link = os.path.join(os.path.realpath(str(link.parent)), link.name)

    def paths(kind, items):
        return " ".join(f"({kind} {sbpl(item)})" for item in items)

    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            "(allow file-write* "
            + paths("subpath", writable)
            + f" (literal {json.dumps(link)})"
            + ' (literal "/dev/null") (literal "/dev/zero") (literal "/dev/ptmx")'
            + ' (regex #"^/dev/ttys[0-9]+$") (literal "/dev/tty")'
            + ' (regex #"^/dev/fd/"))',
            f'(deny file-read* (subpath {sbpl(owner)}) (subpath "/Volumes"))',
            "(allow file-read* " + paths("subpath", readable) + ")",
            "(allow file-read-metadata " + paths("literal", above) + ")",
            "(deny network-outbound (remote unix-socket))",
            "(allow network-outbound (remote unix-socket "
            + paths("subpath", writable)
            + ' (literal "/private/var/run/mDNSResponder")))',
            "(deny lsopen)",
            '(deny mach-lookup (global-name "com.apple.pasteboard.1"))',
            "(deny signal)",
            "(allow signal (target same-sandbox))",
        ]
    )


def start_seatbelt(
    argv, *, sandbox_exec, owner, home, store, tmp, readable, sockets, env, **options
):
    """Start `argv` under `sandbox-exec` with the room's profile, which runs
    it as the process it starts: that process is the sandbox's first. The
    profile goes on the command line, not in a file: a file the room could
    reach would let it loosen the next one. `TMPDIR` is the room's own."""
    for directory in (store, tmp):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    sockets.mkdir(exist_ok=True, mode=0o700)
    profile = seatbelt_profile(
        owner=owner,
        writable=[home, store, sockets],
        readable=readable,
        link=Path(str(sockets) + ".sock"),
    )
    process = subprocess.Popen(
        [sandbox_exec, "-p", profile, *argv],
        start_new_session=True,
        env={**env, "TMPDIR": str(tmp) + "/"},
        **options,
    )
    return process, {"first": process.pid}
