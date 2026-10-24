"""download_artifacts.py retries each request on its own and lays artifacts out the way
actions/download-artifact does with `pattern:` (one directory per artifact)."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import download_artifacts as dl  # noqa: E402


def _zip(name: str, body: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, body)
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadArtifacts(unittest.TestCase):
    def setUp(self):
        os.environ.update(
            GITHUB_TOKEN="t", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="7", GITHUB_API_URL="https://api"
        )
        self.listing = {
            "artifacts": [
                {"id": 1, "name": "backend-pure-0-1", "expired": False},
                {"id": 2, "name": "backend-expected-1", "expired": False},
                {"id": 3, "name": "frontend-1", "expired": False},
            ]
        }
        self.resets = {"list": 2, "blob-1": 1}

    def _urlopen(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if "/artifacts?" in url:
            if self.resets["list"]:
                self.resets["list"] -= 1
                raise urllib.error.URLError(ConnectionResetError("ECONNRESET"))
            return _Response(json.dumps(self.listing).encode())
        blob = url.rsplit("/", 1)[-1]
        if self.resets.get(blob):
            self.resets[blob] -= 1
            raise ConnectionResetError("ECONNRESET")
        return _Response(_zip("report.xml", blob.encode()))

    def _redirect(self, request, timeout=None):
        artifact_id = request.full_url.split("/artifacts/")[1].split("/")[0]
        raise urllib.error.HTTPError(
            request.full_url, 302, "Found", {"Location": f"https://blob/blob-{artifact_id}"}, None
        )

    def test_each_request_is_retried_on_its_own_and_matches_lay_out_per_artifact(self):
        with tempfile.TemporaryDirectory() as dest, mock.patch.object(
            dl.urllib.request, "urlopen", self._urlopen
        ), mock.patch.object(dl._opener, "open", self._redirect), mock.patch.object(
            dl.time, "sleep"
        ) as slept:
            self.assertEqual(dl.main("backend-*", dest), 0)
            got = sorted(p.relative_to(dest).as_posix() for p in Path(dest).rglob("*.xml"))
            self.assertEqual(
                got, ["backend-expected-1/report.xml", "backend-pure-0-1/report.xml"]
            )
            self.assertEqual((Path(dest) / "backend-pure-0-1/report.xml").read_text(), "blob-1")
        # Two resets on the listing, one on one blob: three waits, nothing refetched twice.
        self.assertEqual(slept.call_count, 3)

    def test_a_client_error_is_not_retried(self):
        def forbidden(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with tempfile.TemporaryDirectory() as dest, mock.patch.object(
            dl.urllib.request, "urlopen", forbidden
        ), mock.patch.object(dl.time, "sleep") as slept:
            with self.assertRaises(urllib.error.HTTPError):
                dl.main("backend-*", dest)
        slept.assert_not_called()

    def test_no_matching_artifact_fails(self):
        self.listing["artifacts"] = [{"id": 3, "name": "frontend-1", "expired": False}]
        self.resets["list"] = 0
        with tempfile.TemporaryDirectory() as dest, mock.patch.object(
            dl.urllib.request, "urlopen", self._urlopen
        ):
            self.assertEqual(dl.main("backend-*", dest), 1)


if __name__ == "__main__":
    unittest.main()
