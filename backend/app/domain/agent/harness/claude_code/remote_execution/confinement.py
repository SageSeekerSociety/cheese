"""What a session's sandbox may see and do, shipped in the release and
loaded by the bootstrap when it starts one (`bootstrap.start_sandbox`): on
Linux the bubblewrap command line (`sandbox_argv`) and the seccomp program it
applies, on macOS the Seatbelt profile `sandbox-exec` applies.

Runs on the machine with the Python the executor runs on, as old as 3.9.
"""

import json
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

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


def own_directory(path):
    """``path`` made for this user only, or refused. It is a fixed name in a
    shared /tmp: one somebody else made, or a link to somewhere else, would
    put the platform socket where they can replace it (`runtime.py` guards the
    executor's socket directory the same way)."""
    path.mkdir(exist_ok=True, mode=0o700)
    made = path.lstat()
    if not stat.S_ISDIR(made.st_mode) or made.st_uid != os.getuid():
        # i18n-exempt: an install failure the connector logs, never said in a room
        raise RuntimeError(f"{path} is not this user's own directory")
    path.chmod(0o700)
    return path


def seatbelt_profile(*, owner, writable, readable, link, dialled=()):
    """The macOS sandbox for a room, as `sandbox_argv` builds the
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
    of which act outside the sandbox on its behalf.

    `dialled` (the directory of the platform socket the connector serves) is
    readable and its sockets may be dialled, and nothing in it is writable:
    the room can reach the platform there but not put a socket of its own in
    the connector's place."""
    readable = [*writable, *dialled, *readable]
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
            # One rule per path: several paths in one `remote unix-socket` do
            # not each allow theirs. Measured on macOS 26 (arm64): with the
            # resolver's socket last in a shared rule, no name resolved.
            *(
                f"(allow network-outbound (remote unix-socket {path}))"
                for path in [
                    *(f"(subpath {sbpl(item)})" for item in (*writable, *dialled)),
                    '(literal "/private/var/run/mDNSResponder")',
                ]
            ),
            "(deny lsopen)",
            '(deny mach-lookup (global-name "com.apple.pasteboard.1"))',
            "(deny signal)",
            "(allow signal (target same-sandbox))",
        ]
    )


def start_seatbelt(
    argv,
    *,
    sandbox_exec,
    owner,
    home,
    store,
    tmp,
    readable,
    sockets,
    env,
    platform=None,
    **options,
):
    """Start `argv` under `sandbox-exec` with the room's profile, which runs
    it as the process it starts: that process is the sandbox's first. The
    profile goes on the command line, not in a file: a file the room could
    reach would let it loosen the next one. `TMPDIR` is the room's own."""
    for directory in (store, tmp):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    sockets.mkdir(exist_ok=True, mode=0o700)
    if platform is not None:
        own_directory(platform)
    profile = seatbelt_profile(
        owner=owner,
        writable=[home, store, sockets],
        readable=readable,
        link=Path(str(sockets) + ".sock"),
        dialled=[] if platform is None else [platform],
    )
    process = subprocess.Popen(
        [sandbox_exec, "-p", profile, *argv],
        start_new_session=True,
        env={**env, "TMPDIR": str(tmp) + "/"},
        **options,
    )
    return process, {"first": process.pid}


# Where the machine keeps what is not the system's: mounted disks, and on WSL
# the Windows drives and the socket that runs Windows programs. Hidden in a
# sandbox like the owner's home: they hold the owner's files, and a Windows
# program would run outside it.
HIDDEN_MOUNTS = ("/mnt", "/media", "/run/media", "/run/WSL")


def sandbox_argv(
    argv,
    *,
    bwrap,
    owner,
    home,
    store,
    toolchain,
    tmp,
    releases,
    workdir,
    claude,
    sockets,
    fds,
    platform=None,
    programs=(),
    network=True,
):
    """`argv` run in a bubblewrap sandbox: the room's processes see the machine
    read-only, and of the owner's home only what is bound back below.

    Writable: the room's home and its project's package store. Read-only: the
    machine's toolchain, the release store, the Claude build, and `programs`,
    what the connector placed under the owner's home for the room to run
    where the system had none good enough (its git). Hidden: every
    other room, every other project's store, the owner's own files (the
    connector's credential is among them), the owner's runtime directory,
    whose user bus would start processes outside, and the Docker daemon's
    socket. `/tmp` and `/var/tmp` are a
    directory of the room's own on disk, with the one directory of the
    machine's /tmp that holds this executor's sockets bound back, so the
    connector can still reach it, and the one that holds the platform socket
    the connector serves bound back read-only: the room dials it, and cannot
    unlink it or bind a socket of its own in its place (measured with
    bubblewrap 0.13 on 2026-10-25: a writable bind lets the room replace it).

    Its own pid namespace too: no other room's processes to signal, trace, or
    reach a filesystem through via /proc. That makes the pid an executor
    reports meaningless outside it, which is why it is stopped by request.

    And, with `network`, its own network namespace, empty until
    `start_sandbox` has the machine connect it (`sandbox_host.py`). Without
    it, on a machine a person enrolled, the sandbox shares the machine's
    network. Bubblewrap reports the sandbox's first process on `fds["info"]`
    and waits on `fds["block"]` before running `argv`, under the seccomp
    program on `fds["seccomp"]`.

    On a machine a person enrolled the owner's home holds everything they
    installed there too (nvm, `~/.cargo/bin`, `~/.local/bin`), which a
    session does not see; mounted disks are hidden as well (`HIDDEN_MOUNTS`).
    """
    for directory in (store, toolchain, tmp):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    sockets.mkdir(exist_ok=True, mode=0o700)
    if platform is not None:
        own_directory(platform)
    command = [
        bwrap,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        # No cgroup namespace of its own: one is made when the sandbox
        # starts, before `SANDBOX_HOST` moves it into its limited cgroup, and
        # would leave a runtime that sizes itself by its limit (Node, the JVM)
        # reading a path it cannot resolve.
        *(["--unshare-net"] if network else []),
        "--info-fd",
        str(fds["info"]),
        "--block-fd",
        str(fds["block"]),
        "--seccomp",
        str(fds["seccomp"]),
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        # The boot id as the machine reports it. A container's lxcfs gives it
        # its own, which a fresh /proc does not show, and the environment
        # runner's record of a process (`process_identity`) made inside would
        # then never match the same process read from outside.
        "--ro-bind",
        "/proc/sys/kernel/random/boot_id",
        "/proc/sys/kernel/random/boot_id",
        # Before anything under the owner's home: a home kept under /tmp would
        # otherwise be covered by the session's own /tmp.
        "--bind",
        str(tmp),
        "/tmp",
        "--bind",
        str(tmp),
        "/var/tmp",
    ]
    # Before the owner's home too, which may be kept on one of them.
    for mount in HIDDEN_MOUNTS:
        if Path(mount).is_dir():
            command += ["--tmpfs", mount]
    command += ["--tmpfs", str(owner)]
    names = resolv_conf()
    if network and names != "/etc/resolv.conf":
        # Onto the file it leads to: bwrap mounts on no link (systemd-resolved's).
        command += ["--ro-bind", names, os.path.realpath("/etc/resolv.conf")]
    command += bool(fd := fds.get("hosts")) * ["--ro-bind-data", str(fd), "/etc/hosts"]
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    if runtime_dir.is_dir():
        command += ["--tmpfs", str(runtime_dir)]
    # The docker group's socket is root on the machine, and a user namespace
    # keeps the owner's groups: a session would reach it through a read-only
    # mount as well, since connecting to a socket is not a write to the mount.
    if Path("/run/docker.sock").exists():
        command += ["--ro-bind", "/dev/null", "/run/docker.sock"]
    # The Python the executor runs on, when the person keeps it in their home
    # (pyenv, uv, conda): shown read-only, as the machine's own would be, at
    # the path it is run by as well as where a link there leads.
    prefixes = {Path(sys.base_prefix), Path(sys.prefix)}
    for prefix in sorted(prefixes | {path.resolve() for path in prefixes}):
        if owner in prefix.parents:
            command += ["--ro-bind", str(prefix), str(prefix)]
    for option, path in (
        ("--bind", home),
        ("--bind", store),
        ("--ro-bind", toolchain),
        ("--ro-bind", releases),
        ("--ro-bind", claude.parent),
        *(("--ro-bind", path) for path in programs if path.is_dir()),
    ):
        command += [option, str(path), str(path)]
    command += [
        "--bind",
        str(sockets),
        str(sockets),
        *(() if platform is None else ("--ro-bind", str(platform), str(platform))),
        "--chdir",
        str(workdir),
        "--",
    ]
    return command + argv


# A copy of `sandbox_host.RESOLV_CONFS`, held to it by test_footprint_root.py.
RESOLV_CONFS = ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf")


def resolv_conf():
    """The resolv.conf a sandbox reads: the machine's, unless that names only
    a loopback stub, which inside the sandbox is the sandbox's own loopback
    and answers nothing; then the stub's upstream list, the one
    `sandbox_host.py` lets the sandbox reach."""
    for path in RESOLV_CONFS:
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            fields = line.split()
            if (
                len(fields) >= 2
                and fields[0] == "nameserver"
                and "." in fields[1]
                and not fields[1].startswith("127.")
            ):
                return path
    return RESOLV_CONFS[0]


def loopback_ports(env):
    """The machine's loopback ports a sandbox must reach as its own: the
    backend's, where the machine reaches it through a loopback forward
    (`machine_address.device_api_base`), and the preview tunnel's beside it."""

    ports = []
    for name in ("CHEESE_API", "CHEESE_PREVIEW_URL"):
        address = urlsplit(env.get(name) or "")
        if address.hostname in ("127.0.0.1", "localhost") and address.port:
            if address.port not in ports:
                ports.append(address.port)
    return ports
