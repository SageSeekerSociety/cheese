import base64
import errno
import os
import stat

import pytest

from app.domain.agent.harness.claude_code.remote_execution.forwarded_fs import (
    ForwardedProject,
)


def test_view_refreshes_metadata_and_reads_only_requested_bytes():
    calls = []
    tree = {
        "generation": "one",
        "entries": {
            ".claude": {
                "kind": "directory",
                "mode": 0o700,
                "mtime_ns": 10,
                "size": 0,
                "nlink": 2,
            },
            ".claude/skills/example/SKILL.md": {
                "kind": "file",
                "mode": 0o600,
                "mtime_ns": 11,
                "size": 5,
                "nlink": 1,
            },
        },
        "unsupported_imports": [],
        "unsupported_paths": [],
    }

    def call(method, params):
        calls.append((method, params))
        if params["operation"] == "tree":
            return tree
        return {"data": base64.b64encode(b"ill").decode()}

    view = ForwardedProject(call)
    assert view.refresh()
    assert not view.refresh()
    assert view.readdir("/.claude") == [".", "..", "skills"]
    metadata = view.getattr("/.claude/skills/example/SKILL.md")
    assert stat.S_ISREG(metadata["st_mode"])
    assert view.read("/.claude/skills/example/SKILL.md", 3, 2) == b"ill"
    assert calls[-1] == (
        "context_fs",
        {
            "operation": "read",
            "path": ".claude/skills/example/SKILL.md",
            "offset": 2,
            "size": 3,
        },
    )
    with pytest.raises(OSError) as read_write:
        view.open("/.claude/skills/example/SKILL.md", os.O_RDWR)
    assert read_write.value.errno == errno.EROFS


def test_view_refuses_context_outside_project_boundary():
    view = ForwardedProject(
        lambda *_: {
            "generation": "one",
            "entries": {},
            "unsupported_imports": ["/home/user/instructions.md"],
            "unsupported_paths": [],
        }
    )
    with pytest.raises(RuntimeError, match="leaves the forwarded project boundary"):
        view.refresh()
    view = ForwardedProject(
        lambda _method, params: (
            {
                "generation": "one",
                "entries": {},
                "unsupported_imports": [],
                "unsupported_paths": [],
            }
            if params["operation"] == "tree"
            else {"missing": True}
        )
    )
    with pytest.raises(OSError) as missing:
        view.getattr("/missing")
    assert missing.value.errno == errno.ENOENT


def test_view_maps_absolute_project_symlinks_into_mount():
    view = ForwardedProject(
        lambda *_: {
            "generation": "one",
            "entries": {
                "instructions": {
                    "kind": "symlink",
                    "mode": 0o777,
                    "mtime_ns": 1,
                    "size": 20,
                    "nlink": 1,
                    "target": "/executor/project/docs/instructions.md",
                }
            },
            "unsupported_imports": [],
            "unsupported_paths": [],
        },
        remote_root="/executor/project",
        mountpoint="/center/project",
    )
    view.refresh()
    assert view.readlink("/instructions") == "/center/project/docs/instructions.md"


def test_the_view_looks_up_directories_on_the_executor():
    asked = []
    lookups = {"made": {"mode": 0o755, "mtime_ns": 5, "nlink": 2}}

    def call(method, params):
        if params["operation"] == "tree":
            return {"generation": "g", "entries": {}}
        asked.append(params)
        if params["path"] == "broken":
            raise RuntimeError("link down")
        if params["operation"] == "list":
            return {"directories": ["made"] if params["path"] == "" else []}
        return lookups.get(params["path"], {"missing": True})

    view = ForwardedProject(call)
    attributes = view.getattr("/made")
    assert stat.S_ISDIR(attributes["st_mode"])
    assert not attributes["st_mode"] & 0o222
    with pytest.raises(OSError) as missing:
        view.getattr("/missing")
    assert missing.value.errno == errno.ENOENT
    with pytest.raises(OSError) as broken:
        view.getattr("/broken")
    assert broken.value.errno == errno.EIO
    asked.clear()
    with pytest.raises(OSError):
        view.getattr("/.git/HEAD")
    assert asked == []
    assert set(view.readdir("/")) == {".", "..", ".git", "made"}
