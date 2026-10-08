#!/usr/bin/env python3
"""Offline recovery/freshness contracts; no cloud credentials or boto3 needed."""

import importlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
common = importlib.import_module("r2-common")
check = importlib.import_module("r2-check")
recovery = importlib.import_module("r2-restore")
uploads = importlib.import_module("r2-sync-uploads")


class Store:
    def __init__(self):
        self.data = {}
        self.modified = {}
        self.failed = False
        self.downloaded = []
        self.pages = 0

    def add(self, key, value=b"backup", age=0):
        self.data[key] = value
        self.modified[key] = datetime.fromtimestamp(time.time() - age, timezone.utc)

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        values = [{"Key": key, "Size": len(value), "LastModified": self.modified[key]}
                  for key, value in self.data.items() if key.startswith(Prefix)]
        # Every second entry is on another page; neither newest selection nor
        # file recovery may stop after the first page.
        for page in (values[::2], values[1::2]):
            self.pages += 1
            yield {"Contents": page}

    def download_file(self, bucket, key, path):
        self.downloaded.append(key)
        Path(path).write_bytes(self.data[key])

    def upload_file(self, path, bucket, key):
        if self.failed:
            raise RuntimeError("simulated expired token")
        self.add(key, Path(path).read_bytes())

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.data[Key])}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.data[Key])}


class Backups(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            "CHEESE_R2_ENV": str(self.root / "absent.env"),
            "CHEESE_BACKUP_DIR": str(self.root / "backups"),
            "R2_ENDPOINT": "https://example.invalid", "R2_BUCKET": "backups",
            "R2_ACCESS_KEY_ID": "test", "R2_SECRET_ACCESS_KEY": "test",
        }
        self.store = Store()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env, clear=True).start()
        patch.object(common, "client", return_value=self.store).start()

    def call(self, module, *args):
        with patch.object(sys, "argv", [module.__file__, *args]):
            return module.main()

    def markers(self, age=0):
        directory = Path(self.env["CHEESE_BACKUP_DIR"])
        directory.mkdir(exist_ok=True)
        for name in (".last-offsite-success", ".last-uploads-mirror-success"):
            (directory / name).write_text(f"{int(time.time()) - age}\n")
        return directory

    def remote_backups(self):
        for key in (
            "db/cheese-20261008-050000.dump", "prod-db/cheese-20261008-050000.dump",
            "etrip/etrip-cheese-db-20261008-050000.dump",
            "etrip/etrip-cheesex-db-20261008-050000.dump",
            "etrip/etrip-uploads-20261008-050000.tar",
        ):
            self.store.add(key)

    def test_unconfigured_is_explicit_skip(self):
        with patch.dict(os.environ, {"CHEESE_R2_ENV": self.env["CHEESE_R2_ENV"]}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(self.call(check, "configured"), 3)
                self.assertEqual(self.call(check, "local"), 0)
            self.assertIn("SKIP: R2 is not configured", output.getvalue())

    def test_partial_configuration_is_not_a_skip(self):
        with patch.dict(os.environ, {"CHEESE_R2_ENV": self.env["CHEESE_R2_ENV"], "R2_BUCKET": "x"}, clear=True):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                self.call(check, "configured")

    def test_config_file_quotes_exports_and_environment_override(self):
        config = self.root / "r2.env"
        config.write_text('export R2_BUCKET="file-bucket"\nR2_PREFIX="db" # comment\n')
        os.environ["CHEESE_R2_ENV"] = str(config)
        self.assertEqual(common.load_config()["R2_BUCKET"], "backups")
        self.assertEqual(common.load_config()["R2_PREFIX"], "db")

    def test_local_success_does_not_hide_missing_offsite_or_uploads_marker(self):
        directory = Path(self.env["CHEESE_BACKUP_DIR"])
        directory.mkdir()
        (directory / ".last-success").write_text(str(int(time.time())))
        self.assertEqual(self.call(check, "local"), 1)
        self.markers()
        self.assertEqual(self.call(check, "local"), 0)
        (directory / ".last-offsite-success").unlink()
        self.assertEqual(self.call(check, "local"), 1)

    def test_stale_marker_invalid_marker_and_future_marker_fail(self):
        directory = self.markers(age=6 * 3600 + 1)
        self.assertEqual(self.call(check, "local"), 1)
        self.markers()
        (directory / ".last-uploads-mirror-success").write_text("invalid")
        self.assertEqual(self.call(check, "local"), 1)
        self.markers()
        (directory / ".last-offsite-success").write_text(str(int(time.time()) + 600))
        self.assertEqual(self.call(check, "local"), 1)

    def test_dev_service_receipt_uses_success_time_not_observation_time(self):
        # The old installed writer needs no rollout: a service receipt is enough.
        old = int(time.time()) - 7 * 3600
        show = "Result=success\nExecMainCode=1\nExecMainStatus=0\nExecMainExitTimestamp=old-time\n"
        responses = [subprocess.CompletedProcess([], 0, show, ""),
                     subprocess.CompletedProcess([], 0, str(old) + "\n", "")]
        with patch.object(check.subprocess, "run", side_effect=responses):
            self.assertEqual(self.call(check, "mirror-receipt"), 0)
        marker = common.uploads_marker(common.load_config())
        self.assertEqual(marker.read_text().strip(), str(old))
        self.assertEqual(self.call(check, "local"), 1)

    def test_failed_or_never_run_dev_service_cannot_refresh_marker(self):
        marker = common.uploads_marker(common.load_config())
        marker.parent.mkdir()
        marker.write_text("old-marker")
        for show in ("Result=exit-code\nExecMainCode=1\nExecMainStatus=1\n",
                     "Result=success\nExecMainCode=0\nExecMainStatus=0\n",
                     "Result=success\nExecMainCode=1\nExecMainStatus=0\nExecMainExitTimestamp=n/a\n"):
            with patch.object(check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, show, "")):
                self.assertEqual(self.call(check, "mirror-receipt"), 0)
            self.assertEqual(marker.read_text(), "old-marker")

    def test_remote_read_errors_are_red(self):
        with patch.object(self.store, "paginate", side_effect=RuntimeError("simulated unavailable endpoint")):
            self.assertEqual(self.call(check, "remote"), 1)

    def test_cli_missing_marker_really_exits_nonzero(self):
        result = subprocess.run([sys.executable, str(DEPLOY / "r2-check.py"), "local"],
                                env=os.environ.copy(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing success marker", result.stderr)

    def test_empty_uploads_prefix_preserves_writer_keys_and_does_not_read_whole_bucket(self):
        os.environ["UPLOADS_PREFIX"] = ""
        src = self.root / "uploads"
        src.mkdir()
        (src / "file").write_bytes(b"value")
        self.store.add("db/not-an-upload", b"secret")
        self.assertEqual(self.call(uploads, str(src)), 0)
        self.assertEqual(self.store.data["/file"], b"value")
        dst = self.root / "recovered"
        self.assertEqual(self.call(uploads, "--restore", str(dst), "--verify", str(dst)), 0)
        self.assertEqual(list(dst.iterdir()), [dst / "file"])

    def test_linux_backslash_filename_roundtrips(self):
        if os.name != "posix":
            self.skipTest("POSIX filename contract")
        src = self.root / "uploads"
        src.mkdir()
        (src / "file\\\\name").write_bytes(b"value")
        self.assertEqual(self.call(uploads, str(src)), 0)
        dst = self.root / "recovered"
        self.assertEqual(self.call(uploads, "--restore", str(dst), "--verify", str(dst)), 0)
        self.assertEqual((dst / "file\\\\name").read_bytes(), b"value")

    def test_remote_each_database_and_tar_must_be_fresh(self):
        self.remote_backups()
        self.assertEqual(self.call(check, "remote"), 0)
        for key in tuple(self.store.data):
            with self.subTest(key=key):
                saved = self.store.modified[key]
                self.store.modified[key] = datetime.fromtimestamp(time.time() - 7 * 3600, timezone.utc)
                self.assertEqual(self.call(check, "remote"), 1)
                self.store.modified[key] = saved

    def test_wrong_prefix_other_database_and_empty_dump_do_not_mask_missing_backup(self):
        self.store.add("prod-db/wrong-name.dump")
        self.store.add("etrip/etrip-cheese-db-20261008-050000.dump")
        self.assertEqual(self.call(check, "remote"), 1)
        self.remote_backups()
        self.store.data["db/cheese-20261008-050000.dump"] = b""
        self.assertEqual(self.call(check, "remote"), 1)

    def test_r2_newest_dump_is_downloaded_and_restore_exit_code_is_authoritative(self):
        self.store.add("db/cheese-20261007-050000.dump", age=3600)
        self.store.add("db/cheese-20261008-050000.dump", b"new dump")
        self.store.add("db/not-a-backup.dump")
        files = []

        def restore_command(args):
            self.assertEqual(args[:2], ["bash", str(DEPLOY / "db-restore-test.sh")])
            self.assertEqual(Path(args[2]).read_bytes(), b"new dump")
            files.append(Path(args[2]))
            return subprocess.CompletedProcess(args, 42)

        with patch.object(recovery.subprocess, "run", side_effect=restore_command):
            self.assertEqual(self.call(recovery), 42)
        self.assertEqual(self.store.downloaded, ["db/cheese-20261008-050000.dump"])
        self.assertFalse(files[0].exists())
        self.assertEqual(self.store.pages, 2)

    def test_empty_prefix_and_paginated_selection(self):
        os.environ["R2_PREFIX"] = ""
        self.store.add("cheese-20261008-050000.dump")
        with patch.object(recovery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(self.call(recovery), 0)

    def test_r2_short_download_does_not_run_restore(self):
        self.store.add("db/cheese-20261008-050000.dump")
        with patch.object(self.store, "download_file", side_effect=lambda b, k, p: Path(p).write_bytes(b"short")):
            with patch.object(recovery.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "size"):
                    self.call(recovery)
                run.assert_not_called()

    def test_restore_and_verify_all_uploads_and_reject_same_size_corruption(self):
        self.store.add("prod-uploads/a.bin", b"first")
        self.store.add("prod-uploads/sub/b.bin", b"second")
        dst = self.root / "restored"
        self.assertEqual(self.call(uploads, "--restore", str(dst), "--verify", str(dst)), 0)
        self.assertEqual((dst / "sub/b.bin").read_bytes(), b"second")
        (dst / "a.bin").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "content mismatch"):
            self.call(uploads, "--verify", str(dst))

    def test_restore_refuses_existing_destination_and_path_traversal(self):
        dst = self.root / "restored"
        dst.mkdir()
        (dst / "live-file").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.call(uploads, "--restore", str(dst))
        self.assertEqual((dst / "live-file").read_bytes(), b"keep")
        self.store.add("prod-uploads/../../escape", b"bad")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.call(uploads, "--restore", str(self.root / "new"))
        self.assertFalse((self.root.parent / "escape").exists())

    def test_verify_cannot_follow_symlink_out_of_root(self):
        self.store.add("prod-uploads/file", b"value")
        dst = self.root / "restored"
        dst.mkdir()
        outside = self.root / "secret"
        outside.write_bytes(b"value")
        (dst / "file").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.call(uploads, "--verify", str(dst))

    def test_empty_uploads_prefix_is_not_a_successful_drill(self):
        with self.assertRaisesRegex(ValueError, "no uploads"):
            self.call(uploads, "--restore", str(self.root / "empty"))

    def test_mirror_is_additive_and_marks_only_complete_success(self):
        src = self.root / "uploads"
        src.mkdir()
        (src / "one").write_bytes(b"uploaded")
        self.store.add("prod-uploads/deleted-locally", b"keep")
        self.assertEqual(self.call(uploads, str(src)), 0)
        marker = common.uploads_marker(common.load_config())
        self.assertTrue(marker.is_file())
        self.assertIn("prod-uploads/deleted-locally", self.store.data)
        marker.write_text("old-success")
        (src / "two").write_bytes(b"new")
        self.store.failed = True
        self.assertEqual(self.call(uploads, str(src)), 1)
        self.assertEqual(marker.read_text(), "old-success")
        # A separate successful transcript mirror must not refresh this marker.
        self.store.failed = False
        os.environ["UPLOADS_PREFIX"] = "transcripts"
        self.assertEqual(self.call(uploads, str(src)), 0)
        self.assertEqual(marker.read_text(), "old-success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
