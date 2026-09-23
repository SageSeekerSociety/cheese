#!/usr/bin/env python3
"""Allow an automatic release only if it does not move a running app backwards."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


def workflow_runs(repository: str, workflow: str, candidate: str) -> list[dict]:
    """Read every run for one commit without relying on runner-installed gh."""
    runs = []
    page = 1
    while True:
        query = urlencode({"head_sha": candidate, "branch": "main", "per_page": 100, "page": page})
        request = Request(
            f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/runs?{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def ci_ready(candidate: str) -> bool:
    """Check the latest build and required-CI attempts for this exact main SHA."""
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("the automatic release must name a full commit SHA")
    repository = os.environ["GITHUB_REPOSITORY"]
    ready = True
    for workflow, events in (("build.yml", {"push", "workflow_dispatch"}),
                             ("required-ci.yml", {"push"})):
        runs = [run for run in workflow_runs(repository, workflow, candidate)
                if run["head_sha"] == candidate and run["head_branch"] == "main"
                and run["event"] in events
                and run["head_repository"]["full_name"] == repository]
        # A rerun keeps its run ID. Its latest update must supersede an older
        # success, including when another run for the same SHA was created later.
        latest = max(runs, key=lambda run: (run["updated_at"], run["id"]), default=None)
        if (latest is None or any(run["status"] != "completed" for run in runs)
                or latest["conclusion"] != "success"):
            print(f"Not deploying {candidate}: {workflow} has no successful latest attempt.")
            ready = False
    return ready


def should_skip(candidate: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("the automatic release must name a full commit SHA")
    project = os.environ.get("PROJECT", "cheese")
    repository = os.environ["GITHUB_REPOSITORY"]
    versions = set()
    healthy_services = set()
    all_healthy = True
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
            health = command("docker", "inspect", "--format",
                             "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}", container)
            all_healthy = all_healthy and health == "healthy"
            if health == "healthy":
                healthy_services.add(service)
    if not versions:
        print("No running application containers; allowing bootstrap.")
    all_identical = bool(versions)
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
        all_identical = all_identical and status == "identical"
    if all_identical and all_healthy and healthy_services == {"backend", "frontend"}:
        print(f"Skipping duplicate release {candidate}: both application services are healthy.")
        return True
    return False


def main() -> None:
    if sys.argv[1] == "--require-ci":
        if not ci_ready(sys.argv[2]):
            raise SystemExit("The release commit no longer has successful validation.")
        return
    if sys.argv[1] == "--ci-only":
        key, value = "ready", ci_ready(sys.argv[2])
    else:
        # CI may have been rerun while this job waited for the deploy runner.
        key, value = "skip", not ci_ready(sys.argv[1]) or should_skip(sys.argv[1])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"{key}={str(value).lower()}\n")


if __name__ == "__main__":
    main()
