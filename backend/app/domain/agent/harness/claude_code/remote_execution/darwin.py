"""The macOS half of what the executor asks of the operating system about
processes. Shipped beside runtime.py as ``remote-execution/darwin.py`` and
loaded with ``runpy``, like ``portable.py``, only on macOS.

Not `ps`: on macOS it is setuid root, and a process under a Seatbelt profile,
as a room's executor is, may not exec a setuid program (EPERM, whatever the
profile allows). libproc answers from inside the sandbox.
"""

import ctypes
import struct
import sys

assert sys.platform == "darwin"

_libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
_PROC_ALL_PIDS = 1
_PROC_PIDTBSDINFO = 3
# struct proc_bsdinfo, with the parent pid at 16.
_BSDINFO_SIZE = 136
_PPID_AT = 16


def processes():
    """(pid, parent pid) of every process of this user's."""
    # proc_listpids answers in bytes; room is left for the processes started
    # between the two calls.
    needed = _libc.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    pids = (ctypes.c_int * (max(needed, 0) // 4 + 64))()
    filled = _libc.proc_listpids(_PROC_ALL_PIDS, 0, pids, ctypes.sizeof(pids))
    info = ctypes.create_string_buffer(_BSDINFO_SIZE)
    rows = []
    for pid in pids[: max(filled, 0) // 4]:
        if pid > 0 and _BSDINFO_SIZE == _libc.proc_pidinfo(
            pid, _PROC_PIDTBSDINFO, ctypes.c_uint64(0), info, _BSDINFO_SIZE
        ):
            rows.append((pid, struct.unpack_from("<I", info, _PPID_AT)[0]))
    return rows
