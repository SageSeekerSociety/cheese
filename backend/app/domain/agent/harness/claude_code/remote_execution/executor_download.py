"""Fetching the executor onto a machine over a link that may drop.

Shipped in the release (`runtime_core.RELEASE_FILES`) and run by
`bootstrap.binary` only when a machine has no proven executor of its own, so
nothing here weighs on a warm room's preparation.
"""

import gzip
import hashlib
import shutil
import sys
import time
import urllib.request
import uuid
import zlib
from urllib.error import HTTPError


def megabytes(count):
    return "?" if count is None else f"{count / 1e6:.1f} MB"


def download(url, destination):
    """Fetch the executor from `url` into a fresh file beside `destination`,
    checked against the digest the platform names for it.

    The executor is a quarter of a gigabyte and the whole install is given two
    minutes (`lease_claim.INSTALL_MOST_S`): on a slow or interrupted link one
    attempt cannot fetch it, and an attempt that starts from nothing never
    will. So what arrives is kept in one partial file beside the destination,
    across attempts and across the kill at that deadline, and the next attempt
    asks only for the rest. The packed copy is asked for and kept packed, so
    the offsets of every attempt are into the same bytes; whether what arrived
    is packed is read off the file, since a resumed answer need not repeat its
    encoding. Bytes that do not add up to the digest — a partial left by a
    different copy, a corrupted transfer — are thrown away, and the attempt
    after that starts over.

    Whatever stops it, the last line it leaves says where it was fetching from
    and how far it got: that line is all of its stderr the room is told."""
    suffix = ".exe" if sys.platform == "win32" else ""
    partial = destination.with_name(destination.name + ".partial")
    have = partial.stat().st_size if partial.exists() else 0
    total = None

    def ask(start):
        headers = {"Accept-Encoding": "gzip"}
        if start:
            headers["Range"] = f"bytes={start}-"
        request = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(request, timeout=300)

    def account(said):
        return (
            f"downloading the executor from {url}: {said} after "
            f"{megabytes(have)} of {megabytes(total)}, which the next attempt "
            "continues from"
        )

    try:
        try:
            response = ask(have)
        except HTTPError as refused:
            # Nothing left past what is held: the copy held is longer than the
            # one served, so it is not a part of it.
            if refused.code != 416:
                raise
            partial.unlink()
            have = 0
            response = ask(0)
        with response:
            if response.status == 206:
                span = response.headers["Content-Range"].split()[1]
                first, _, size = span.replace("-", "/", 1).split("/")
                if int(first) != have:
                    raise RuntimeError(f"the server resumed at byte {first}")
                total = int(size)
                mode = "ab"
            else:
                have = 0
                length = response.headers.get("Content-Length")
                total = int(length) if length else None
                mode = "wb"
            digest = response.headers.get("X-Checksum-SHA256")
            if not digest:
                raise RuntimeError("the server named no checksum for it")
            with partial.open(mode) as output:
                said = time.monotonic()
                while chunk := response.read(1 << 20):
                    output.write(chunk)
                    have += len(chunk)
                    if time.monotonic() - said >= 10:
                        # A slow link is cut off at the install's deadline with
                        # no error of its own: this is then its last line.
                        output.flush()
                        print(account("still going"), file=sys.stderr, flush=True)
                        said = time.monotonic()
        if total is not None and have < total:
            raise RuntimeError("the connection closed early")
    except Exception as stopped:  # whatever stopped it, said with how far it got
        raise RuntimeError(account(f"stopped ({stopped})")) from stopped
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}{suffix}")
    try:
        with partial.open("rb") as held:
            packed = held.read(2) == b"\x1f\x8b"
        if packed:
            with gzip.GzipFile(partial, "rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, 1 << 20)
            partial.unlink()
        else:
            partial.replace(temporary)
        hasher = hashlib.sha256()
        with temporary.open("rb") as written:
            for chunk in iter(lambda: written.read(1 << 20), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != digest.lower():
            raise ValueError(
                f"sha256 {hasher.hexdigest()[:12]}…, expected {digest[:12]}…"
            )
    except (gzip.BadGzipFile, EOFError, zlib.error, ValueError) as wrong:
        partial.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"the executor downloaded from {url} is not the one the platform "
            f"serves ({wrong}); it was discarded and the next attempt fetches it "
            "again"
        ) from wrong
    except BaseException:
        # Kept what arrived, as a full disk or a kill mid-unpack leaves it.
        temporary.unlink(missing_ok=True)
        raise
    return temporary
