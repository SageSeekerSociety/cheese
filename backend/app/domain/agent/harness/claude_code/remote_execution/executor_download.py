"""Fetching the executor onto a machine over a link that may drop.

Shipped in the release (`runtime_core.RELEASE_FILES`) and run by
`bootstrap.binary` only when a machine has no proven executor of its own, so
nothing here weighs on a warm room's preparation.
"""

import gzip
import hashlib
import json
import shutil
import sys
import time
import urllib.request
import uuid
import zlib
from urllib.error import HTTPError


def megabytes(count):
    return "?" if count is None else f"{count / 1e6:.1f} MB"


# How often the download says how far it has got, for the room to show while
# the install runs (`machine.install_progress`).
PROGRESS_EVERY_S = 2


def progress(have, total):
    """One line saying how far the download has got, for the platform to read
    off stderr while the install runs. Only a download whose size is known
    has a "how far"."""
    if total is not None:
        line = json.dumps({"executor_download": {"have": have, "total": total}})
        print(line, file=sys.stderr, flush=True)


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

    start = have
    try:
        try:
            response = ask(start)
        except HTTPError as refused:
            if refused.code != 416:
                raise
            # Nothing past what is held. Held at exactly the served copy's
            # size, it is whole — the attempt that fetched it was stopped
            # before unpacking it — and its last byte is asked for again, for
            # the digest the answer carries. Held longer, it is not a part of
            # the copy served, and is fetched again.
            served = (refused.headers.get("Content-Range") or "").rpartition("/")[2]
            if served == str(have):
                start = have - 1
            else:
                partial.unlink()
                have = start = 0
            response = ask(start)
        with response:
            if response.status == 206:
                span = response.headers["Content-Range"].split()[1]
                first, _, size = span.replace("-", "/", 1).split("/")
                if int(first) != start:
                    # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                    raise RuntimeError(f"the server resumed at byte {first}")
                total = int(size)
                mode = "ab"
            else:
                have = start = 0
                length = response.headers.get("Content-Length")
                total = int(length) if length else None
                mode = "wb"
            digest = response.headers.get("X-Checksum-SHA256")
            if not digest:
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise RuntimeError("the server named no checksum for it")
            with partial.open(mode) as output:
                # The byte asked for again is already held.
                response.read(have - start)
                said = shown = time.monotonic()
                progress(have, total)
                while chunk := response.read(1 << 20):
                    output.write(chunk)
                    have += len(chunk)
                    if time.monotonic() - shown >= PROGRESS_EVERY_S:
                        progress(have, total)
                        shown = time.monotonic()
                    if time.monotonic() - said >= 10:
                        # A slow link is cut off at the install's deadline with
                        # no error of its own: this is then its last line.
                        output.flush()
                        print(account("still going"), file=sys.stderr, flush=True)
                        said = time.monotonic()
                progress(have, total)
        if total is not None and have < total:
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise RuntimeError("the connection closed early")
    except Exception as stopped:  # whatever stopped it, said with how far it got
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
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
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise ValueError(
                f"sha256 {hasher.hexdigest()[:12]}…, expected {digest[:12]}…"
            )
    except (gzip.BadGzipFile, EOFError, zlib.error, ValueError) as wrong:
        partial.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
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
