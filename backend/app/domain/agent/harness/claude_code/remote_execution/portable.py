"""The Windows half of what the device programs ask of the operating system.

Shipped beside runtime.py as ``remote-execution/portable.py`` and loaded — with
``runpy``, like every sibling these programs use — only when they run on
Windows. POSIX keeps calling fcntl, signals and process groups directly, so
nothing here is on a Linux or macOS code path.

Standard library only, ctypes included: the runtime a Windows machine is given
is the embeddable Python with nothing installed into it.

What Windows lacks, and what stands in for it here:

* ``flock`` — a byte-range lock through ``msvcrt.locking``. The byte sits far
  past the end of the file, because a locked range cannot be read by anyone
  else and some of the files locked this way are logs a person reads.
* process groups and ``SIGTERM`` — a process tree walked from the parent pids a
  snapshot reports, each member ended with ``TerminateProcess``. Windows reuses
  pids and keeps a dead parent's pid in its children, so a process only counts
  as a child when it was created after the parent it names. Git Bash breaks the
  Windows chain on every exec: the program a forked shell execs is the child of
  that fork, which exits at once — `sleep` in `sleep 60 &` has a parent nobody
  can find. Its own process table keeps the link, so the tree also follows what
  Git Bash's `ps` says.
* ``/proc/<pid>/cmdline`` and ``cwd`` — read from the process itself, which is
  how a task's marker and a room's working directory are found.
"""

import ctypes
import os
import shutil
import subprocess
import sys
import time

# Nothing below exists anywhere else; the assertion is also what tells a type
# checker running on another platform not to read the rest.
assert sys.platform == "win32"

import msvcrt  # noqa: E402
from ctypes import wintypes  # noqa: E402

# Far past any file this locks, and within what the C runtime's 32-bit seek can
# reach.
LOCK_OFFSET = 0x7FFFFFF0

# A daemon gets a console with no window rather than no console at all: every
# console program a DETACHED process starts is given a fresh, visible console,
# while children of a hidden console share it.
DAEMON_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

_PROCESS_TERMINATE = 0x0001
_PROCESS_VM_READ = 0x0010
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002
_WAIT_TIMEOUT = 0x102
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_PROCESS_BASIC_INFORMATION = 0
_PROCESS_COMMAND_LINE_INFORMATION = 60
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _BasicInformation(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_void_p),
        ("BasePriority", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
    ctypes.POINTER(wintypes.FILETIME)
] * 4
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_ntdll.NtQueryInformationProcess.restype = ctypes.c_ulong
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]


def lock(file, blocking=True):
    """``flock(LOCK_EX)``, or ``LOCK_EX | LOCK_NB`` when not blocking.

    Released when the file is closed. Raises BlockingIOError, as flock does,
    when not blocking and somebody else holds it. ``LK_LOCK`` is not used for
    the blocking case because it gives up with an error after ten seconds.
    """
    descriptor = file.fileno()
    while True:
        os.lseek(descriptor, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if not blocking:
                raise BlockingIOError(str(exc)) from exc
            time.sleep(0.05)


def which(argv):
    """argv with its program looked up on PATH.

    CreateProcess looks in System32 before PATH, and System32 holds a ``bash``
    that is WSL's, and a ``curl`` and a ``tar`` of its own. A name has to be
    resolved the way a POSIX exec would resolve it before it is started.
    """
    return [shutil.which(argv[0]) or argv[0], *argv[1:]]


def popen_daemon(argv, **options):
    """Start a process that outlives the one starting it.

    It leaves the starter's job object when that job allows it, because a
    connector that ends its jobs on exit would otherwise end the daemon too. A
    job that forbids leaving refuses the whole creation, and then the process
    is started inside it: that is where it would have been anyway.
    """
    try:
        return subprocess.Popen(
            argv, creationflags=DAEMON_FLAGS | CREATE_BREAKAWAY_FROM_JOB, **options
        )
    except PermissionError:
        return subprocess.Popen(argv, creationflags=DAEMON_FLAGS, **options)


def _open(pid, access):
    return _kernel32.OpenProcess(access, False, pid) or None


def _created(handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def _command_line(handle):
    size = wintypes.ULONG(4096)
    while True:
        buffer = ctypes.create_string_buffer(size.value)
        status = _ntdll.NtQueryInformationProcess(
            handle,
            _PROCESS_COMMAND_LINE_INFORMATION,
            buffer,
            size,
            ctypes.byref(size),
        )
        if status == _STATUS_INFO_LENGTH_MISMATCH:
            continue
        if status:
            return ""
        text = _UnicodeString.from_buffer(buffer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""


def _image(handle):
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return ""
    return buffer.value


def _read(handle, address, size):
    buffer = ctypes.create_string_buffer(size)
    done = ctypes.c_size_t()
    if not _kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(done)
    ):
        raise OSError(ctypes.get_last_error(), "ReadProcessMemory")
    return buffer.raw[: done.value]


def _directory(pid):
    """The working directory of a 64-bit process, read out of its PEB."""
    handle = _open(pid, _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ)
    if handle is None:
        return ""
    try:
        basic = _BasicInformation()
        if _ntdll.NtQueryInformationProcess(
            handle,
            _PROCESS_BASIC_INFORMATION,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
            None,
        ):
            return ""
        # PEB.ProcessParameters, then RTL_USER_PROCESS_PARAMETERS.CurrentDirectory,
        # whose first member is the DosPath UNICODE_STRING (x64 and ARM64 layout).
        parameters = int.from_bytes(
            _read(handle, basic.PebBaseAddress + 0x20, 8), "little"
        )
        header = _read(handle, parameters + 0x38, 16)
        length = int.from_bytes(header[0:2], "little")
        address = int.from_bytes(header[8:16], "little")
        return _read(handle, address, length).decode("utf-16-le")
    except (OSError, TypeError):
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _msys_table():
    """(pid, ppid, Windows pid) for every process Git Bash knows of.

    The `ps` on the connector's PATH is Git's own, and it sees the processes of
    that one installation — the one every shell the platform starts is from.
    """
    program = shutil.which("ps")
    if program is None:
        return []
    try:
        listing = subprocess.run(
            [program, "-e", "-l"], capture_output=True, text=True, timeout=15
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    lines = listing.splitlines()
    if not lines or "WINPID" not in lines[0]:
        return []
    rows = []
    for line in lines[1:]:
        fields = line.split()
        # A status letter can precede the pid.
        if fields and not fields[0].isdigit():
            fields = fields[1:]
        if len(fields) >= 4 and all(f.isdigit() for f in fields[:4]):
            rows.append((int(fields[0]), int(fields[1]), int(fields[3])))
    return rows


def _msys_parents():
    """{child's Windows pid: its Git Bash parent's Windows pid}."""
    rows = _msys_table()
    windows = {pid: winpid for pid, _, winpid in rows}
    return {winpid: windows[parent] for _, parent, winpid in rows if parent in windows}


def windows_pid(msys_pid):
    """The Windows pid behind a pid a Git Bash script recorded (`$!`), or None.

    The two number spaces overlap, so one is never used as the other."""
    return next((w for pid, _, w in _msys_table() if pid == msys_pid), None)


def processes(*, directories=False):
    """Every process this user can see, as dicts.

    ``pid``, ``ppid`` (as the snapshot reports it — see ``tree``),
    ``msys_parent`` (the Windows pid of its Git Bash parent, or None),
    ``created`` (a FILETIME, or None when the process could not be opened),
    ``command`` (its command line) and ``image`` (its executable); ``cwd`` too
    when asked for, since reading it costs two more reads of its memory.
    """
    msys = _msys_parents()
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    rows = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            rows.append({"pid": entry.th32ProcessID, "ppid": entry.th32ParentProcessID})
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    for row in rows:
        row.update(created=None, command="", image="", msys_parent=msys.get(row["pid"]))
        handle = _open(row["pid"], _PROCESS_QUERY_LIMITED_INFORMATION)
        if handle is None:
            continue
        try:
            row.update(
                created=_created(handle),
                command=_command_line(handle),
                image=_image(handle),
            )
        finally:
            _kernel32.CloseHandle(handle)
        if directories:
            row["cwd"] = _directory(row["pid"])
    return rows


def tree(roots, rows=None):
    """``roots`` — (pid, created) pairs — and all their descendants, parents first.

    ``created`` is passed rather than looked up so that a root that has
    already exited still finds the children it left behind.
    """
    rows = processes() if rows is None else rows
    children, adopted = {}, {}
    for row in rows:
        if row["created"] is not None:
            children.setdefault(row["ppid"], []).append(row)
        if row.get("msys_parent") is not None:
            # Git Bash's own parent link is to a live process by construction.
            adopted.setdefault(row["msys_parent"], []).append(row)
    found, queue = [], list(roots)
    while queue:
        pid, created = queue.pop(0)
        if pid in found:
            continue
        found.append(pid)
        for child in children.get(pid, []):
            if created is None or child["created"] >= created:
                queue.append((child["pid"], child["created"]))
        for child in adopted.get(pid, []):
            queue.append((child["pid"], child["created"]))
    return found


def identity(pid):
    """A string that names this process and no later one with its pid, or ""."""
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE)
    if handle is None:
        return ""
    try:
        if _kernel32.WaitForSingleObject(handle, 0) != _WAIT_TIMEOUT:
            return ""
        created = _created(handle)
        return f"windows:{created}" if created is not None else ""
    finally:
        _kernel32.CloseHandle(handle)


def created(pid):
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return None
    try:
        return _created(handle)
    finally:
        _kernel32.CloseHandle(handle)


def command_line(pid):
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return ""
    try:
        return _command_line(handle)
    finally:
        _kernel32.CloseHandle(handle)


def terminate(pids):
    """End each process now. There is no gentler request a console-less
    process can be sent, so this is SIGKILL, never SIGTERM."""
    for pid in pids:
        handle = _open(pid, _PROCESS_TERMINATE)
        if handle is None:
            continue
        try:
            _kernel32.TerminateProcess(handle, 1)
        finally:
            _kernel32.CloseHandle(handle)


def terminate_tree(pid, started=None):
    """A process and everything it started, taken once and then ended.

    ``started`` is the process's creation time when the caller recorded it,
    which is what still finds the children of a process that has exited.
    """
    terminate(tree([(pid, created(pid) if started is None else started)]))
