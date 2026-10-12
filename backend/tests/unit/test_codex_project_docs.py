import base64
from pathlib import Path

from app.domain.agent.harness.codex.tools import RemoteTools


class Context:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.entries = {
            name: {"kind": "file", "size": len(data), "mtime_ns": index}
            for index, (name, data) in enumerate(files.items(), 1)
        }

    def call(self, method: str, params: dict):
        assert method == "context_fs"
        if params["operation"] == "tree":
            return {"entries": self.entries}
        data = self.files[params["path"]]
        start = params["offset"]
        end = start + params["size"]
        return {"data": base64.b64encode(data[start:end]).decode()}


def tools(context: Context, root: Path) -> RemoteTools:
    result = RemoteTools.__new__(RemoteTools)
    result.client = context
    result.project_docs = root
    result.docs_mirrored = None
    return result


def test_project_instructions_keep_codex_native_paths_and_bytes(tmp_path):
    context = Context(
        {
            "AGENTS.md": b"root rules\n",
            "pkg/AGENTS.md": b"nested rules\n",
            "pkg/AGENTS.override.md": b"nested override\n",
            "docs/README.md": b"not instructions\n",
        }
    )
    root = tmp_path / "codex-workspace"
    mirror = tools(context, root)

    assert mirror.sync_project_docs(context.entries) is True
    assert (root / "AGENTS.md").read_bytes() == b"root rules\n"
    assert (root / "pkg/AGENTS.md").read_bytes() == b"nested rules\n"
    assert (root / "pkg/AGENTS.override.md").read_bytes() == b"nested override\n"
    assert not (root / "docs/README.md").exists()
    assert mirror.sync_project_docs(context.entries) is False


def test_project_instruction_snapshot_removes_stale_files(tmp_path):
    context = Context({"AGENTS.md": b"current\n"})
    root = tmp_path / "codex-workspace"
    (root / "old").mkdir(parents=True)
    (root / "old/AGENTS.md").write_text("old\n")
    mirror = tools(context, root)

    assert mirror.sync_project_docs(context.entries) is True
    assert not (root / "old/AGENTS.md").exists()


def test_project_instructions_reject_paths_outside_workspace(tmp_path):
    outside = tmp_path / "outside/AGENTS.md"
    entries = (
        {"../AGENTS.md": b"parent traversal\n"},
        {"nested/../../AGENTS.md": b"noncanonical traversal\n"},
        {str(outside): b"absolute path\n"},
    )

    for index, files in enumerate(entries):
        root = tmp_path / f"codex-workspace-{index}"
        mirror = tools(Context(files), root)

        assert mirror.sync_project_docs(mirror.client.entries) is False

    assert not (tmp_path / "AGENTS.md").exists()
    assert not outside.exists()
