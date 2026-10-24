"""download_artifacts.py retries each request on its own and lays artifacts out the way
actions/download-artifact does with `pattern:` (one directory per artifact).

Every case asserts what comes out — files on disk, or the error — against a fake GitHub
whose behaviour makes the wrong implementation fail: a blob that serves only once
catches a whole-batch retry, a blob host that refuses the token catches a forwarded
Authorization header."""

import http.client
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


class FakeGitHub:
    """The listing, the zip endpoint (302 to a blob) and the blob host."""

    def __init__(self, names):
        self.artifacts = [{"id": i, "name": n, "expired": False} for i, n in enumerate(names, 1)]
        self.list_resets = 0
        self.blob_faults: dict[str, list] = {}  # blob -> exceptions raised in turn
        self.serve_once: set[str] = set()
        self.served: set[str] = set()

    def urlopen(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if "/artifacts?" in url:
            if self.list_resets:
                self.list_resets -= 1
                raise urllib.error.URLError(ConnectionResetError("ECONNRESET"))
            return _Response(json.dumps({"artifacts": self.artifacts}).encode())
        # The blob host: a signed URL that must not carry the GitHub token.
        if not isinstance(request, str) and request.get_header("Authorization"):
            raise urllib.error.HTTPError(url, 400, "Authorization not allowed", {}, None)
        blob = url.rsplit("/", 1)[-1]
        faults = self.blob_faults.get(blob)
        if faults:
            raise faults.pop(0)
        if blob in self.serve_once and blob in self.served:
            raise urllib.error.HTTPError(url, 410, "Gone", {}, None)
        self.served.add(blob)
        return _Response(_zip("report.xml", blob.encode()))

    def zip_endpoint(self, request, timeout=None):
        artifact_id = request.full_url.split("/artifacts/")[1].split("/")[0]
        raise urllib.error.HTTPError(
            request.full_url, 302, "Found", {"Location": f"https://blob/blob-{artifact_id}"}, None
        )

    def patched(self):
        return [
            mock.patch.object(dl.urllib.request, "urlopen", self.urlopen),
            mock.patch.object(dl._opener, "open", self.zip_endpoint),
            mock.patch.object(dl.time, "sleep"),
        ]


class DownloadArtifacts(unittest.TestCase):
    def setUp(self):
        os.environ.update(
            GITHUB_TOKEN="t", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="7", GITHUB_API_URL="https://api"
        )
        dl._started = dl.time.monotonic()
        self.github = FakeGitHub(["backend-pure-0-1", "backend-expected-1", "frontend-1"])
        self.dest = Path(tempfile.mkdtemp())
        for patch in self.github.patched():
            patch.start()
            self.addCleanup(patch.stop)

    def _files(self):
        return sorted(p.relative_to(self.dest).as_posix() for p in self.dest.rglob("*.xml"))

    def test_lays_out_one_directory_per_matching_artifact(self):
        self.assertEqual(dl.main("backend-*", str(self.dest)), 0)
        self.assertEqual(self._files(), ["backend-expected-1/report.xml", "backend-pure-0-1/report.xml"])
        self.assertEqual((self.dest / "backend-pure-0-1/report.xml").read_text(), "blob-1")

    def test_a_reset_retries_only_that_request(self):
        # Artifacts download in name order: backend-expected-1 (blob-2), then
        # backend-pure-0-1 (blob-1). blob-2, fetched first, can be served only once;
        # blob-1 resets once. A whole-batch retry would fetch blob-2 again and get 410.
        self.github.list_resets = 2
        self.github.serve_once = {"blob-2"}
        self.github.blob_faults["blob-1"] = [ConnectionResetError("ECONNRESET")]
        self.assertEqual(dl.main("backend-*", str(self.dest)), 0)
        self.assertEqual(len(self._files()), 2)

    def test_a_cut_short_body_is_retried(self):
        self.github.blob_faults["blob-1"] = [http.client.IncompleteRead(b"partial", 100)]
        self.assertEqual(dl.main("backend-*", str(self.dest)), 0)
        self.assertEqual(len(self._files()), 2)

    def test_the_token_never_reaches_the_blob_host(self):
        # The fake blob host answers 400 to a request carrying Authorization.
        self.assertEqual(dl.main("backend-*", str(self.dest)), 0)

    def test_the_zip_endpoint_redirect_is_not_followed(self):
        # urllib follows a 302 with the request's headers, Authorization included; the
        # opener used for the zip endpoint must hand the redirect back instead.
        request = dl._api("/repos/o/r/actions/artifacts/1/zip")
        self.assertIsNone(
            dl._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://blob/x")
        )

    def test_a_client_error_is_not_retried(self):
        # A 403 that a retry would get past still fails: client errors are final.
        self.github.blob_faults["blob-1"] = [
            urllib.error.HTTPError("https://blob/blob-1", 403, "Forbidden", {}, None)
        ]
        with self.assertRaises(urllib.error.HTTPError):
            dl.main("backend-*", str(self.dest))

    def test_no_retry_past_the_deadline(self):
        # Out of time: a reset that the next attempt would get past still fails the step.
        dl._started = dl.time.monotonic() - dl.DEADLINE_S
        self.github.blob_faults["blob-1"] = [ConnectionResetError("ECONNRESET")]
        with self.assertRaises(ConnectionResetError):
            dl.main("backend-*", str(self.dest))

    def test_a_burst_longer_than_a_few_tries_is_ridden_out(self):
        # Eight resets in a row on one blob (a burst) and then it answers: the step
        # succeeds while the budget lasts, however many tries that takes.
        self.github.blob_faults["blob-1"] = [ConnectionResetError("ECONNRESET")] * 8
        self.assertEqual(dl.main("backend-*", str(self.dest)), 0)
        self.assertEqual(len(self._files()), 2)

    def test_no_matching_artifact_fails(self):
        self.assertEqual(dl.main("nothing-*", str(self.dest)), 1)


if __name__ == "__main__":
    unittest.main()
