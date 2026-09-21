"""Migration must account for native processes as well as Docker mounts."""

from pathlib import Path
import runpy
import tempfile
import unittest


workspace_users = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "check-forge-workspace-writers.py")
)["workspace_users"]


class WorkspaceWritersTest(unittest.TestCase):
    def test_native_worktree_and_open_file_users_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces"
            task = workspace / ".worktrees" / "project" / "task"
            task.mkdir(parents=True)
            sibling = root / "workspaces-other"
            sibling.mkdir()
            proc = root / "proc"
            for pid, cwd in ((101, task), (102, root), (103, sibling)):
                process = proc / str(pid)
                (process / "fd").mkdir(parents=True)
                (process / "cwd").symlink_to(cwd)
            (proc / "102" / "fd" / "3").symlink_to(task / "report.txt")
            self.assertEqual(workspace_users(workspace, proc), [101, 102])
            (proc / "101" / "cwd").unlink()
            (proc / "102" / "fd" / "3").unlink()
            self.assertEqual(workspace_users(workspace, proc), [])

    def test_missing_process_inventory_does_not_certify_no_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                workspace_users(root / "workspaces", root / "missing-proc")


if __name__ == "__main__":
    unittest.main()
