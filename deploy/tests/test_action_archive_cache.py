import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "action_cache", Path(__file__).parents[1] / "ci-runner/cache-action-archives.py"
)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class ActionCacheTests(unittest.TestCase):
    def test_pins_include_subpaths_and_ignore_unpinned_actions(self):
        sha = "a" * 40
        text = f"  - uses: owner/repo/subpath@{sha}\n  - uses: actions/checkout@v4\n  - uses: ./local\n"
        self.assertEqual(cache.PIN.findall(text), [("owner/repo", sha)])

    def test_two_slots_share_one_download_and_reruns_download_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            dirs = [Path(temporary) / "slot0", Path(temporary) / "slot1"]
            pins = [("owner/repo", "a" * 40)]
            with patch.object(
                cache,
                "download",
                side_effect=lambda url, path: path.write_bytes(b"archive"),
            ) as download:
                self.assertEqual(cache.populate(pins, dirs), 7)
                self.assertEqual(cache.populate(pins, dirs), 0)
                self.assertEqual(download.call_count, 1)
            files = [next(directory.rglob("*.tar.gz")) for directory in dirs]
            self.assertTrue(files[0].samefile(files[1]))

    def test_failed_download_is_not_published_and_can_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pins = [("owner/repo", "a" * 40)]

            def fail(url, path):
                path.write_bytes(b"partial")
                raise OSError("interrupted")

            with (
                patch.object(cache, "download", side_effect=fail),
                self.assertRaises(OSError),
            ):
                cache.populate(pins, [directory])
            self.assertEqual(list(directory.rglob("*.tar.gz")), [])
            self.assertEqual(list(directory.rglob("*.partial")), [])
            with patch.object(
                cache,
                "download",
                side_effect=lambda url, path: path.write_bytes(b"complete"),
            ):
                self.assertEqual(cache.populate(pins, [directory]), 8)

    def test_invalid_response_retried_then_valid_archive_accepted(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            info = tarfile.TarInfo("action/file")
            info.size = 2
            archive.addfile(info, io.BytesIO(b"ok"))
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "archive"
            with (
                patch.object(
                    cache.urllib.request,
                    "urlopen",
                    side_effect=[io.BytesIO(b"error"), io.BytesIO(data.getvalue())],
                ) as request,
                patch.object(cache.time, "sleep"),
            ):
                cache.download("https://example.invalid/archive", target)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(target.read_bytes(), data.getvalue())


if __name__ == "__main__":
    unittest.main()
