import argparse
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PIN = re.compile(
    r"^\s*(?:-\s*)?uses:\s*['\"]?"
    r"([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([0-9a-f]{40})\b",
    re.MULTILINE,
)


def report(message):
    print(f"{datetime.now(UTC).isoformat()} {message}", flush=True)


def workflow_pins(repository, revision, token):
    owner, name = repository.split("/")
    query = """
    query($owner: String!, $name: String!, $path: String!) {
      repository(owner: $owner, name: $name) {
        object(expression: $path) {
          ... on Tree { entries { name object { ... on Blob { text } } } }
        }
      }
    }
    """
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(
            {
                "query": query,
                "variables": {
                    "owner": owner,
                    "name": name,
                    "path": f"{revision}:.github/workflows",
                },
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError(result["errors"])
    entries = result["data"]["repository"]["object"]["entries"]
    pins = set()
    for entry in entries:
        if entry["name"].endswith((".yml", ".yaml")):
            pins.update(PIN.findall(entry["object"]["text"]))
    if not pins:
        raise RuntimeError("No pinned actions found")
    return sorted(pins)


def download(url, target):
    for attempt in range(1, 4):
        try:
            with (
                urllib.request.urlopen(url, timeout=120) as response,
                target.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            with tarfile.open(target, "r:gz") as archive:
                if not archive.getmembers():
                    raise ValueError("Empty action archive")
            return
        except (OSError, urllib.error.URLError, tarfile.TarError) as error:
            report(f"url={url} attempt={attempt} status=failed error={error}")
            if attempt == 3:
                raise
            time.sleep(attempt * 2)


def populate(pins, directories):
    downloaded = 0
    for repository, revision in pins:
        targets = [
            directory / repository.replace("/", "_") / f"{revision}.tar.gz"
            for directory in directories
        ]
        source = next((path for path in targets if path.is_file()), None)
        if source is None:
            source = targets[0]
            source.parent.mkdir(parents=True, exist_ok=True)
            report(f"action={repository}@{revision} status=download-start")
            with tempfile.NamedTemporaryFile(
                dir=source.parent, suffix=".partial", delete=False
            ) as output:
                candidate = Path(output.name)
            try:
                download(
                    f"https://codeload.github.com/{repository}/tar.gz/{revision}",
                    candidate,
                )
                candidate.replace(source)
            finally:
                candidate.unlink(missing_ok=True)
            downloaded += source.stat().st_size
            report(
                f"action={repository}@{revision} status=downloaded bytes={source.stat().st_size}"
            )
        else:
            report(
                f"action={repository}@{revision} status=cache-hit bytes={source.stat().st_size}"
            )
        for target in targets:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
    report(
        f"status=complete actions={len(pins)} cache_directories={len(directories)} downloaded_bytes={downloaded}"
    )
    return downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--cache-dir", type=Path, action="append", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("--revision must be a full commit SHA")
    report(f"repository={args.repository} revision={args.revision} status=start")
    pins = workflow_pins(args.repository, args.revision, os.environ["GH_TOKEN"])
    populate(pins, args.cache_dir)


if __name__ == "__main__":
    main()
