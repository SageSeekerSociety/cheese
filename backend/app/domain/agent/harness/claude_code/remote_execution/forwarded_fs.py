"""Read-only project context view backed by the executor transport."""

import base64
import errno
import os
import stat
import sys


class ForwardedProject:
    def __init__(self, call, *, remote_root=None, mountpoint=None):
        self.call = call
        self.remote_root = remote_root
        self.mountpoint = mountpoint
        self.generation = None
        self.entries = {}

    def refresh(self):
        tree = self.call("context_fs", {"operation": "tree"})
        unsupported = tree.get("unsupported_imports", []) + tree.get(
            "unsupported_paths", []
        )
        if unsupported:
            raise RuntimeError(
                "Project context leaves the forwarded project boundary: "
                + ", ".join(unsupported)
            )
        changed = tree["generation"] != self.generation
        if changed:
            self.entries = tree["entries"]
            self.generation = tree["generation"]
        return changed

    @staticmethod
    def _name(path):
        return path.lstrip("/")

    @classmethod
    def _workflow_name(cls, path, *, allow_claude=False):
        name = cls._name(path)
        if allow_claude and name == ".claude":
            return name
        if name != ".claude/workflows" and not name.startswith(".claude/workflows/"):
            raise OSError(errno.EROFS, name)
        return name

    def getattr(self, path, handle=None):
        self.refresh()
        name = self._name(path)
        if not name or name == ".git":
            return {
                "st_mode": stat.S_IFDIR | 0o500,
                "st_nlink": 2,
                "st_size": 0,
                "st_mtime_ns": 0,
                "st_mtime": 0,
                "st_ctime": 0,
                "st_atime": 0,
            }
        entry = self.entries.get(name)
        if entry is None:
            raise OSError(errno.ENOENT, name)
        kind = {
            "directory": stat.S_IFDIR,
            "file": stat.S_IFREG,
            "symlink": stat.S_IFLNK,
        }[entry["kind"]]
        mode = entry["mode"]
        if name not in (".claude", ".claude/workflows") and not name.startswith(
            ".claude/workflows/"
        ):
            mode &= ~0o222
        return {
            "st_mode": kind | mode,
            "st_nlink": entry["nlink"],
            "st_size": entry["size"],
            "st_mtime_ns": entry["mtime_ns"],
            "st_mtime": entry["mtime_ns"] / 1_000_000_000,
            "st_ctime": entry["mtime_ns"] / 1_000_000_000,
            "st_atime": entry["mtime_ns"] / 1_000_000_000,
        }

    def readdir(self, path, handle=None):
        self.refresh()
        parent = self._name(path)
        prefix = parent + "/" if parent else ""
        children = {
            name[len(prefix) :].split("/", 1)[0]
            for name in self.entries
            if name.startswith(prefix) and name != parent
        }
        if not parent:
            children.add(".git")
        return [".", "..", *sorted(children)]

    def read(self, path, size, offset, handle=None):
        name = self._name(path)
        entry = self.entries.get(name)
        if entry is None or entry["kind"] != "file":
            raise OSError(errno.ENOENT, name)
        result = self.call(
            "context_fs",
            {"operation": "read", "path": name, "offset": offset, "size": size},
        )
        return base64.b64decode(result["data"])

    def readlink(self, path):
        self.refresh()
        name = self._name(path)
        entry = self.entries.get(name)
        if entry is None or entry["kind"] != "symlink":
            raise OSError(errno.EINVAL, name)
        target = entry["target"]
        if (
            target.startswith("/")
            and self.remote_root
            and self.mountpoint
            and (
                target == self.remote_root or target.startswith(self.remote_root + "/")
            )
        ):
            return self.mountpoint + target[len(self.remote_root) :]
        return target

    def open(self, path, flags):
        if flags & (os.O_WRONLY | os.O_RDWR):
            self._workflow_name(path)
            if flags & os.O_TRUNC:
                self.truncate(path, 0)
        self.getattr(path)
        return 0

    def access(self, path, mode):
        if mode & os.W_OK and self._name(path) != ".claude":
            self._workflow_name(path)
        self.getattr(path)
        return 0

    def mkdir(self, path, mode):
        name = self._workflow_name(path, allow_claude=True)
        self.call("context_fs", {"operation": "mkdir", "path": name})
        self.refresh()
        return 0

    def create(self, path, mode, handle=None):
        name = self._workflow_name(path)
        self.call("context_fs", {"operation": "create", "path": name})
        self.refresh()
        return 0

    def write(self, path, data, offset, handle=None):
        name = self._workflow_name(path)
        result = self.call(
            "context_fs",
            {
                "operation": "write",
                "path": name,
                "offset": offset,
                "data": base64.b64encode(data).decode(),
            },
        )
        self.refresh()
        return result["written"]

    def truncate(self, path, size, handle=None):
        name = self._workflow_name(path)
        self.call("context_fs", {"operation": "truncate", "path": name, "size": size})
        self.refresh()
        return 0

    def rename(self, old, new):
        source = self._workflow_name(old)
        destination = self._workflow_name(new)
        self.call(
            "context_fs",
            {"operation": "rename", "path": source, "destination": destination},
        )
        self.refresh()
        return 0

    def unlink(self, path):
        name = self._workflow_name(path)
        self.call("context_fs", {"operation": "unlink", "path": name})
        self.refresh()
        return 0

    def rmdir(self, path):
        name = self._workflow_name(path)
        self.call("context_fs", {"operation": "rmdir", "path": name})
        self.refresh()
        return 0

    def opendir(self, path):
        self.getattr(path)
        return 0

    def release(self, path, handle):
        return 0

    def releasedir(self, path, handle):
        return 0

    def getxattr(self, path, name, position=0):
        return b""

    def listxattr(self, path):
        return []

    def statfs(self, path):
        return self.call("context_fs", {"operation": "statfs"})


def mount(target_path, mountpoint):
    import json
    from pathlib import Path

    from client import RemoteClient
    from fuse import FUSE, Operations

    target_path = Path(target_path)
    target = json.loads(target_path.read_text())
    tree_path = target_path.with_name("context-tree.json")
    client = RemoteClient(target)

    def call(method, params):
        client.config = json.loads(target_path.read_text())
        view.remote_root = client.config["workspace"]
        if method == "context_fs" and params["operation"] == "tree":
            return json.loads(tree_path.read_text())
        return client.call(method, params)

    class FuseProject(ForwardedProject, Operations):
        def __init__(self, call):
            ForwardedProject.__init__(
                self,
                call,
                remote_root=target["workspace"],
                mountpoint=mountpoint,
            )

    view = FuseProject(call)
    view.refresh()
    FUSE(
        view,
        mountpoint,
        foreground=True,
        ro=False,
        nothreads=False,
        direct_io=True,
        attr_timeout=0,
        entry_timeout=0,
        negative_timeout=0,
    )


if __name__ == "__main__":
    mount(sys.argv[1], sys.argv[2])
