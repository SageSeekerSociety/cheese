#!/usr/bin/env python3
"""Allow an automatic release only if it does not move a running app backwards."""

import os
from pathlib import Path
import re
import subprocess
import sys


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


def should_skip(candidate: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("the automatic release must name a full commit SHA")
    project = os.environ.get("PROJECT", "cheese")
    repository = os.environ["GITHUB_REPOSITORY"]
    versions = set()
    for service in ("backend", "frontend"):
        containers = command(
            "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.service={service}",
        ).split()
        for container in containers:
            # An unchanged image gets a new release tag but retains its old OCI
            # revision label. Config.Image records the tag actually deployed.
            image = command("docker", "inspect", "--format", "{{.Config.Image}}", container)
            tag = image.rsplit(":", 1)[-1]
            if "@" in image or not re.fullmatch(r"[0-9a-f]{7,40}", tag):
                raise ValueError(f"cannot identify the running {service} release from its image tag")
            versions.add(tag)
    if not versions:
        print("No running application containers; allowing bootstrap.")
    for version in sorted(versions):
        status = command(
            "gh", "api", f"repos/{repository}/compare/{version}...{candidate}", "--jq", ".status",
        )
        if status == "behind":
            print(f"Skipping automatic release {candidate}: running release {version} is newer.")
            return True
        if status not in ("ahead", "identical"):
            raise ValueError(f"candidate {candidate} is not a descendant of running release {version}: {status}")
        print(f"Candidate {candidate} is {status} relative to running release {version}.")
    return False


def main() -> None:
    skip = should_skip(sys.argv[1])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"skip={str(skip).lower()}\n")


if __name__ == "__main__":
    main()
