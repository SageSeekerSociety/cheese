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
from contextlib import redirect_stdout, redirect_stderr
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


class OffsiteCadence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offsite-cadence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.remote = self.root / "remote"
        self.remote.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.now = 1793379600
        tools = {
            "pg_dump": '#!/bin/sh\nwhile [ "$1" != -f ]; do shift; done\necho readable-dump > "$2"\n',
            "pg_restore": "#!/bin/sh\necho 'TABLE DATA public x'\n",
            "date": f'#!{sys.executable}\nimport os, sys\nfrom datetime import datetime\n'
                    'print(datetime.fromtimestamp(int(os.environ["BACKUP_TEST_NOW"])).strftime(sys.argv[1][1:]))\n',
        }
        for name, text in tools.items():
            path = bin_dir / name
            path.write_text(text)
            path.chmod(0o755)
        (self.root / "backend.env").write_text("DATABASE_URL=postgresql://fake/db\n")
        (self.root / "r2.env").write_text("R2_BUCKET=fake\n")
        uploader = self.root / "upload.py"
        uploader.write_text(
            'import os, sys\nfrom pathlib import Path\n'
            'if os.environ.get("BACKUP_TEST_FAIL") == "1": raise SystemExit(1)\n'
            'p = Path(sys.argv[1])\n'
            '(Path(os.environ["BACKUP_TEST_REMOTE"]) / p.name).write_bytes(p.read_bytes())\n'
        )
        self.env = {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "CHEESE_BACKUP_DIR": str(self.backups),
            "CHEESE_BACKUP_ENV": str(self.root / "backup.env"),
            "CHEESE_ENV_FILE": str(self.root / "backend.env"),
            "CHEESE_R2_ENV": str(self.root / "r2.env"),
            "CHEESE_R2_UPLOAD": str(uploader), "CHEESE_VENV_PY": sys.executable,
            "BACKUP_TEST_REMOTE": str(self.remote),
        }

    def run_backup(self, age_hours=0, seconds=0, **extra):
        env = {**self.env, "BACKUP_TEST_NOW": str(self.now + age_hours * 3600 + seconds), **extra}
        result = subprocess.run(["bash", str(DEPLOY / "db-backup.sh")],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.backups / ".last-success").exists())
        return result

    def test_hourly_local_dumps_only_go_offsite_once_six_hours_have_elapsed(self):
        self.run_backup()
        first = (self.backups / ".last-offsite-success").read_text()
        self.run_backup(1)
        self.run_backup(5)
        self.run_backup(5, seconds=3599)
        self.assertEqual(len(list(self.backups.glob("*.dump"))), 4)
        self.assertEqual(len(list(self.remote.glob("*.dump"))), 1)
        self.assertEqual((self.backups / ".last-offsite-success").read_text(), first)
        self.run_backup(6)
        self.assertEqual(len(list(self.remote.glob("*.dump"))), 2)
        self.assertEqual(int((self.backups / ".last-offsite-success").read_text()), self.now + 6 * 3600)

    def test_failed_due_upload_retries_the_next_hour_and_keeps_success_time(self):
        self.run_backup()
        first = (self.backups / ".last-offsite-success").read_text()
        self.run_backup(6, BACKUP_TEST_FAIL="1")
        self.assertEqual((self.backups / ".last-offsite-success").read_text(), first)
        self.run_backup(7)
        self.run_backup(8)
        self.assertEqual(len(list(self.remote.glob("*.dump"))), 2)
        self.assertEqual(int((self.backups / ".last-offsite-success").read_text()), self.now + 7 * 3600)

    def test_interval_is_configurable_and_one_hour_preserves_hourly_uploads(self):
        (self.root / "backup.env").write_text("CHEESE_BACKUP_OFFSITE_INTERVAL_HOURS=1\n")
        self.run_backup()
        self.run_backup(1)
        self.assertEqual(len(list(self.remote.glob("*.dump"))), 2)

    def test_missing_invalid_or_future_success_time_attempts_upload(self):
        for marker in (None, "broken\n", str(self.now + 3600)):
            with self.subTest(marker=marker):
                path = self.backups / ".last-offsite-success"
                path.unlink(missing_ok=True)
                if marker is not None:
                    path.write_text(marker)
                self.run_backup()
                self.assertEqual(int(path.read_text()), self.now)


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
        # Expected negative cases must not create false Actions error annotations.
        with patch.object(sys, "argv", [module.__file__, *args]), redirect_stderr(io.StringIO()):
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
                self.store.modified[key] = datetime.fromtimestamp(time.time() - 9 * 3600, timezone.utc)
                self.assertEqual(self.call(check, "remote"), 1)
                self.store.modified[key] = saved

    def test_dev_freshness_allows_interval_and_two_hours_but_other_backups_stay_at_six(self):
        directory = self.markers()
        self.remote_backups()
        now = int(time.time())
        with patch.object(check.time, "time", return_value=now):
            for interval in (6, 12):
                with self.subTest(interval=interval), patch.dict(os.environ, {
                    "CHEESE_BACKUP_OFFSITE_INTERVAL_HOURS": str(interval),
                }):
                    age = (interval + 2) * 3600
                    (directory / ".last-offsite-success").write_text(str(now - age))
                    key = "db/cheese-20261008-050000.dump"
                    self.store.modified[key] = datetime.fromtimestamp(now - age, timezone.utc)
                    self.assertEqual(self.call(check, "local"), 0)
                    self.assertEqual(self.call(check, "remote"), 0)
                    (directory / ".last-offsite-success").write_text(str(now - age - 1))
                    self.store.modified[key] = datetime.fromtimestamp(now - age - 1, timezone.utc)
                    self.assertEqual(self.call(check, "local"), 1)
                    self.assertEqual(self.call(check, "remote"), 1)
            self.markers()
            self.remote_backups()
            (directory / ".last-uploads-mirror-success").write_text(str(now - 6 * 3600 - 1))
            self.assertEqual(self.call(check, "local"), 1)
            for key in tuple(self.store.data):
                if key.startswith("db/"):
                    continue
                with self.subTest(key=key):
                    saved = self.store.modified[key]
                    self.store.modified[key] = datetime.fromtimestamp(now - 6 * 3600 - 1, timezone.utc)
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


class TieredDumpRetention(unittest.TestCase):
    """dev retention: hourly for 7 days, then one per day to 30; never delete unverified."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dump-retention-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dir = self.root / "backups"
        self.dir.mkdir()
        self.log = self.root / "prune.log"
        self.store = Store()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {
            "CHEESE_R2_ENV": str(self.root / "absent.env"),
            "R2_ENDPOINT": "https://example.invalid", "R2_BUCKET": "backups",
            "R2_ACCESS_KEY_ID": "test", "R2_SECRET_ACCESS_KEY": "test",
        }, clear=True).start()
        patch.object(common, "client", return_value=self.store).start()
        self.prune = importlib.import_module("r2-prune-dumps")
        year, month, day = time.localtime()[:3]
        # mktime normalises the day field, so this stays on the intended
        # calendar day across a DST change.
        self.day = lambda days_ago, hour: time.mktime((year, month, day - days_ago, hour, 0, 0, 0, 0, -1))

    def dump(self, at, content=b"dump", remote=True):
        name = time.strftime("cheese-%Y%m%d-%H%M%S.dump", time.localtime(at))
        (self.dir / name).write_bytes(content)
        if remote is True:
            self.store.add(f"db/{name}", content)
        elif remote:
            self.store.add(f"db/{name}", remote)
        return name

    def run_prune(self, *extra):
        argv = ["r2-prune-dumps.py", str(self.dir), "--hourly-days", "7",
                "--retention-days", "30", "--log", str(self.log), *extra]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return self.prune.main()

    def local(self):
        return sorted(path.name for path in self.dir.iterdir())

    def test_every_dump_inside_seven_days_stays_even_without_r2_copy(self):
        names = [self.dump(time.time() - hours * 3600, remote=False) for hours in (1, 2, 30, 31, 6 * 24 + 20)]
        self.assertEqual(self.run_prune(), 0)
        self.assertEqual(self.local(), sorted(names))
        self.assertEqual(self.store.data, {})

    def test_older_extra_dump_is_deleted_only_once_its_r2_copy_matches(self):
        verified = self.dump(self.day(10, 10))
        mismatched = self.dump(self.day(10, 11), remote=b"truncated")
        daily = self.dump(self.day(10, 12))
        self.store.failed = True  # the re-upload of the mismatched copy does not land
        self.assertEqual(self.run_prune(), 3)
        self.assertEqual(self.local(), sorted([mismatched, daily]))
        self.assertNotIn(verified, self.local())

    def test_missing_r2_copy_is_uploaded_and_kept_until_verified(self):
        missing = self.dump(self.day(12, 3), b"only on the box", remote=False)
        daily = self.dump(self.day(12, 23))
        self.store.failed = True
        self.assertEqual(self.run_prune(), 3)
        self.assertEqual(self.local(), sorted([missing, daily]))
        self.store.failed = False
        self.assertEqual(self.run_prune(), 0)
        self.assertEqual(self.store.data[f"db/{missing}"], b"only on the box")
        self.assertEqual(self.local(), [daily])
        lines = self.log.read_text().splitlines()
        self.assertTrue(any(f"uploaded {missing}" in line and f"key=db/{missing}" in line for line in lines))

    def test_exactly_one_dump_per_day_survives_beyond_seven_days(self):
        kept = []
        for days_ago in (8, 15, 29):
            for hour in (0, 9, 23):
                name = self.dump(self.day(days_ago, hour))
            kept.append(name)  # the last dump of that day
        expired = self.dump(self.day(35, 23))
        self.assertEqual(self.run_prune(), 0)
        self.assertEqual(self.local(), sorted(kept))
        self.assertNotIn(expired, self.local())

    def test_dry_run_deletes_and_uploads_nothing(self):
        names = [self.dump(self.day(9, 1)), self.dump(self.day(9, 2), remote=False),
                 self.dump(self.day(9, 3)), self.dump(self.day(40, 1))]
        before = dict(self.store.data)
        self.assertEqual(self.run_prune("--dry-run"), 0)
        self.assertEqual(self.local(), sorted(names))
        self.assertEqual(self.store.data, before)

    def test_unreadable_r2_listing_deletes_no_unverified_dump(self):
        names = [self.dump(self.day(9, 1)), self.dump(self.day(9, 2))]
        with patch.object(self.store, "paginate", side_effect=RuntimeError("endpoint down")):
            self.assertEqual(self.run_prune(), 3)
        self.assertEqual(self.local(), sorted(names))

    def test_short_re_upload_keeps_the_local_dump(self):
        short = self.dump(self.day(11, 4), b"full local dump", remote=False)
        daily = self.dump(self.day(11, 22))
        with patch.object(self.store, "upload_file",
                          side_effect=lambda path, bucket, key: self.store.add(key, b"part")):
            self.assertEqual(self.run_prune(), 3)
        self.assertEqual(self.local(), sorted([short, daily]))

    def test_dump_between_thirty_and_thirty_one_days_old_is_not_expired(self):
        # The horizon matches `find -mtime +30`: whole days, so 30.5 days stays.
        name = self.dump(time.time() - 30.5 * 86400)
        self.assertEqual(self.run_prune(), 0)
        self.assertEqual(self.local(), [name])

    def test_daily_copy_is_one_with_a_verified_r2_copy(self):
        # A dump cut short by a kill has no matching R2 copy; it must not be the
        # day's survivor at the cost of the good ones.
        good = self.dump(self.day(9, 10))
        survivor = self.dump(self.day(9, 11))
        truncated = self.dump(self.day(9, 12), b"cut", remote=b"never uploaded whole")
        self.store.failed = True
        self.assertEqual(self.run_prune(), 3)
        self.assertEqual(self.local(), sorted([survivor, truncated]))
        self.assertNotIn(good, self.local())

    def test_local_retention_thins_unuploaded_dumps_without_creating_offsite_objects(self):
        env = self.backup_script_env()
        recent = self.dump(time.time() - 3600, remote=False)
        kept = [recent]
        for days_ago in (8, 15, 29):
            for hour in (1, 2, 3):
                daily = self.dump(self.day(days_ago, hour), remote=False)
            kept.append(daily)
        self.dump(self.day(35, 1), remote=False)
        before = dict(self.store.data)
        with patch.dict(os.environ, {"PATH": env["PATH"]}):
            self.assertEqual(self.run_prune("--local-only"), 0)
        self.assertEqual(self.local(), sorted(kept))
        self.assertEqual(self.store.data, before)

    def test_local_retention_keeps_readable_daily_dump_even_when_r2_is_down(self):
        env = self.backup_script_env()
        self.dump(self.day(9, 1), remote=False)
        good = self.dump(self.day(9, 2), remote=False)
        self.dump(self.day(9, 3), content=b"cut", remote=False)
        with patch.dict(os.environ, {"PATH": env["PATH"]}), patch.object(
            self.store, "paginate", side_effect=RuntimeError("endpoint down"),
        ):
            self.assertEqual(self.run_prune("--local-only"), 0)
        self.assertEqual(self.local(), [good])
        self.assertEqual(self.store.data, {})

    def backup_script(self, backup_env=None, r2_env=None, pruner=None):
        for path in self.dir.iterdir():
            path.unlink()
        hourly = self.dump(self.day(10, 1), remote=False)
        self.dump(self.day(10, 2), remote=False)
        expired = self.dump(self.day(40, 1), remote=False)
        stamp = self.day(40, 1)
        os.utime(self.dir / expired, (stamp, stamp))
        env = self.backup_script_env()
        if backup_env:
            (self.root / "backup.env").write_text(backup_env)
        if r2_env:
            (self.root / "r2.env").write_text(r2_env)
            env["CHEESE_R2_ENV"] = str(self.root / "r2.env")
        if pruner:
            env["CHEESE_R2_PRUNE"] = str(pruner)
        result = subprocess.run(["bash", str(DEPLOY / "db-backup.sh")], env=env,
                                capture_output=True, text=True)
        return result, hourly, expired

    def test_backup_script_without_dev_setting_keeps_the_plain_thirty_day_prune(self):
        # prod has no ~/ops/backup.env; its retention must not change.
        result, hourly, expired = self.backup_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(hourly, self.local())
        self.assertNotIn(expired, self.local())

    def test_backup_script_unit_stays_green_when_pruner_succeeds_or_only_kept_unverified_dumps(self):
        # An R2 timeout that leaves a dump on the box is safe, not a failed backup.
        for code in (0, 3):
            with self.subTest(pruner_exit=code):
                pruner = self.root / f"exit{code}.py"
                pruner.write_text(f"raise SystemExit({code})\n")
                result, hourly, expired = self.backup_script(
                    backup_env="CHEESE_BACKUP_HOURLY_DAYS=7\n", pruner=pruner)
                self.assertNotIn(expired, self.local())
                self.assertIn(hourly, self.local())
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_backup_script_with_dev_setting_and_failing_pruner_still_expires_and_fails(self):
        crash = self.root / "crash.py"
        crash.write_text("raise SystemExit('pruner crashed')\n")
        result, hourly, expired = self.backup_script(
            backup_env="CHEESE_BACKUP_HOURLY_DAYS=7\n", pruner=crash)
        self.assertNotIn(expired, self.local())
        self.assertIn(hourly, self.local())
        self.assertNotEqual(result.returncode, 0)

    def test_local_retention_and_hourly_backup_succeed_without_working_r2_config(self):
        result, hourly, expired = self.backup_script(
            backup_env="CHEESE_BACKUP_HOURLY_DAYS=7\n", r2_env="R2_BUCKET=backups\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(expired, self.local())
        self.assertNotIn(hourly, self.local())
        self.assertEqual(len(list(self.dir.glob("cheese-*.dump"))), 2)

    def backup_script_env(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "pg_dump").write_text('#!/bin/sh\nwhile [ "$1" != -f ]; do shift; done\necho dump > "$2"\n')
        (bin_dir / "pg_restore").write_text(
            '#!/bin/sh\n[ "$(cat "$2")" != cut ] || exit 1\necho "TABLE DATA public x"\n')
        for tool in ("pg_dump", "pg_restore"):
            (bin_dir / tool).chmod(0o755)
        (self.root / "backend.env").write_text("DATABASE_URL=postgresql://fake/db\n")
        return {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "CHEESE_BACKUP_DIR": str(self.dir), "CHEESE_ENV_FILE": str(self.root / "backend.env"),
            "CHEESE_BACKUP_ENV": str(self.root / "backup.env"),
            "CHEESE_R2_ENV": str(self.root / "absent.env"), "CHEESE_VENV_PY": sys.executable,
            "CHEESE_R2_PRUNE": str(DEPLOY / "r2-prune-dumps.py"),
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
