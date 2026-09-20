"""本机目录授权 — letting the assistant work in a folder on the user's own machine.

A device is PURE COMPUTE: it runs the assistant's turns and touches no files but
its own workspace. This package adds the one thing that crosses that line — an
explicit, revocable, directory-scoped grant that lets a turn read (or read and
write) a named folder on that machine, with every access recorded.

Three facts shape the whole package:

* The grant names a directory, never a machine or a disk. ``C:/`` and ``/``
  are refused at the door (see ``paths.normalize`` for the mechanism and
  ``service.grant_directory`` for the refusal).
* The platform is not the enforcement point. The files live on the user's
  machine, so the daemon enforces too, from its own copy of the grant set
  (``cli/internal/localfs``). What lives here is the authoritative record, the
  decision the platform makes *before* it asks the device for anything, and the
  audit trail. Neither side alone is trusted.
* Nothing is shared by granting. A grant is an access key to somebody's disk, so
  it must never travel with an outcome, an attachment or a clone. That discipline
  is enforced by a test, not by a comment: see
  ``tests/unit/test_local_fs_sharing_guard.py``.
"""

from app.domain.local_fs.records import (
    AccessRecord,
    DirectoryGrant,
    GrantMode,
    GrantScope,
)

__all__ = [
    "AccessRecord",
    "DirectoryGrant",
    "GrantMode",
    "GrantScope",
]
