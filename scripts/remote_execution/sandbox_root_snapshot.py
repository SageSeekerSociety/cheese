#!/usr/bin/env python3
"""Measure root backup and restore using two disposable cloud VMs.

Run save on the source and restore on the destination, with the same prepared
image and release. --transfer names a private JSON file: upload URL on save;
image, download URL, size and MD5 on restore. Never publish that file.
"""

import argparse
import hashlib
import json
import runpy
import time
import urllib.request
from pathlib import Path

from sandbox_image import Sandbox, disk_bytes


def put_archive(path, url):
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    with path.open("rb") as source:
        request = urllib.request.Request(
            url,
            data=source,
            method="PUT",
            headers={"Content-Length": str(size), "Content-Type": "application/gzip"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            assert response.status == 200
    return {"size": size, "md5": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save", "restore"))
    for name in ("release", "claude", "output", "transfer"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    transfer = json.loads(args.transfer.read_text())
    rootfs = runpy.run_path(str(args.release / "remote-execution/rootfs_snapshot.py"))
    confinement = runpy.run_path(str(args.release / "remote-execution/confinement.py"))
    images = runpy.run_path(str(args.release / "remote-execution/sandbox_image.py"))
    sandbox = Sandbox(args.release, args.claude, args.image, args.output, 4096)
    receipt = {"image": args.image, "quota_mib": 4096}
    try:
        if args.action == "save":
            receipt["ready_s"] = sandbox.start()
            receipt["install"] = sandbox.command(
                "apt-get update && apt-get install -y postgresql-client ffmpeg"
            )
            assert receipt["install"]["exit"] == 0, receipt["install"]
            receipt["root_edits"] = sandbox.command(
                "printf 'root-snapshot-ok\\n' > /etc/root-snapshot-sentinel && "
                "mkdir -p /opt/root-snapshot && printf '#!/bin/sh\\necho restored-tool\\n' > /opt/root-snapshot/tool && "
                "chmod 755 /opt/root-snapshot/tool && ln /opt/root-snapshot/tool /opt/root-snapshot/link && "
                "install -d -m 700 -o 1234 -g 2345 /opt/root-snapshot/private && "
                "printf service-owned > /opt/root-snapshot/private/data && chown 1234:2345 /opt/root-snapshot/private/data && "
                "rm /usr/bin/yes && rm -rf /usr/share/doc/bash && mkdir /usr/share/doc/bash && touch /usr/share/doc/bash/new"
            )
            assert receipt["root_edits"]["exit"] == 0
            receipt["upper_bytes"] = disk_bytes(
                rootfs["layer_for"](sandbox.home, args.image) / "upper"
            )
            sandbox.stop()
            started = time.monotonic()
            with images["namespace"]() as (userns, _):
                receipt["snapshot"] = rootfs["save"](
                    sandbox.home,
                    args.image,
                    transfer["url"],
                    bwrap=str(sandbox.owner / ".cheese/runtime/bubblewrap/bwrap"),
                    namespace=confinement["image_user_namespace"](userns),
                    fds=(userns,),
                    put_archive=put_archive,
                )
            receipt["snapshot_s"] = round(time.monotonic() - started, 4)
            assert receipt["snapshot"]["status"] == "saved", receipt["snapshot"]
        else:
            receipt["ready_with_restore_s"] = sandbox.start(snapshot=transfer)
            receipt["restore"] = sandbox.started["root_snapshot"]
            receipt["correctness"] = sandbox.command(
                "psql --version && ffmpeg -v error -f lavfi -i sine=frequency=1000:duration=0.1 -f null - && "
                "cat /etc/root-snapshot-sentinel && /opt/root-snapshot/tool && /opt/root-snapshot/link && "
                "test \"$(stat -c '%u:%g' /opt/root-snapshot/private/data)\" = 1234:2345 && "
                'test "$(cat /opt/root-snapshot/private/data)" = service-owned && '
                "test ! -e /usr/bin/yes && test -e /usr/share/doc/bash/new && test ! -e /usr/share/doc/bash/changelog.Debian.gz && "
                "stat -c '%u:%g %i %n' /opt/root-snapshot/tool /opt/root-snapshot/link && echo restore-correct"
            )
            assert receipt["correctness"]["exit"] == 0, receipt["correctness"]
    finally:
        sandbox.close()
        (args.output / "result.json").write_text(json.dumps(receipt, indent=2))
    print(
        json.dumps(
            {
                key: value
                for key, value in receipt.items()
                if key not in {"install", "root_edits"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
