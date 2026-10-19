#!/usr/bin/env python3
"""A session mirror leaves the box only once the bucket holds the same bytes."""

import hashlib
import importlib
import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
archiver = importlib.import_module("archive-session-mirrors")

DAY = 86400


class NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class Bucket:
    def __init__(self, *, corrupt=False):
        self.objects = {}
        self.corrupt = corrupt

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        data = Path(path).read_bytes()
        if self.corrupt:
            data = data[:-1]
        self.objects[key] = (data, dict((ExtraArgs or {}).get("Metadata", {})))

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        data, meta = self.objects[Key]
        # The digest a real bucket returns is the one the uploader stored; a
        # truncated body keeps it, so only the size can betray the damage.
        return {"ContentLength": len(data), "Metadata": meta}


class Archive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mirrors-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def mirror(self, name, *, age_days, shm_age_days=None):
        directory = self.root / "p" / "room" / "claude-code" / name
        directory.mkdir(parents=True)
        db = directory / "records.sqlite"
        db.write_bytes(name.encode() * 100)
        shm = directory / "records.sqlite-shm"
        shm.write_bytes(b"\0" * 32)
        (directory / "records.sqlite-wal").write_bytes(b"")
        then = time.time() - age_days * DAY
        os.utime(db, (then, then))
        shm_then = time.time() - (age_days if shm_age_days is None else shm_age_days) * DAY
        os.utime(shm, (shm_then, shm_then))
        return db

    def run_archive(self, bucket, *, apply=True):
        with redirect_stdout(io.StringIO()):
            return archiver.archive(self.root, s3=bucket, bucket="private", prefix="archive/x",
                                    older_than_s=7 * DAY, apply=apply, held=set())

    def test_a_stale_mirror_leaves_only_after_the_bucket_holds_it(self):
        db = self.mirror("old", age_days=10, shm_age_days=0)
        content = db.read_bytes()
        bucket = Bucket()
        totals = self.run_archive(bucket)
        self.assertFalse(db.parent.exists())
        stored, meta = bucket.objects["archive/x/p/room/claude-code/old/records.sqlite"]
        self.assertEqual(stored, content)
        self.assertEqual(meta["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(totals["archived"], 1)

    def test_a_mirror_whose_upload_does_not_read_back_stays(self):
        db = self.mirror("old", age_days=10)
        totals = self.run_archive(Bucket(corrupt=True))
        self.assertTrue(db.exists())
        self.assertEqual(totals["failed"], 1)

    def test_a_recently_written_mirror_stays(self):
        db = self.mirror("new", age_days=1)
        bucket = Bucket()
        self.run_archive(bucket)
        self.assertTrue(db.exists())
        self.assertEqual(bucket.objects, {})

    def test_a_dry_run_changes_nothing(self):
        db = self.mirror("old", age_days=10)
        bucket = Bucket()
        self.run_archive(bucket, apply=False)
        self.assertTrue(db.exists())
        self.assertEqual(bucket.objects, {})

    def test_a_mirror_held_open_stays(self):
        db = self.mirror("old", age_days=10)
        with redirect_stdout(io.StringIO()):
            archiver.archive(self.root, s3=Bucket(), bucket="private", prefix="archive/x",
                             older_than_s=7 * DAY, apply=True, held={str(db)})
        self.assertTrue(db.exists())


if __name__ == "__main__":
    unittest.main()
