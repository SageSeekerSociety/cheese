#!/usr/bin/env python3
"""Allow an automatic release only if it does not move a running app backwards."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


# The required /readyz checks that fail on the box rather than in the build. A
# backend unready over these alone stays unready through a release of the same
# build: the release would only roll back and add a failed run.
DEPENDENCY_CHECKS = frozenset({"database", "redis"})


def unready_dependencies(container: str) -> set[str]:
    """The dependencies a backend is unready over, or an empty set when it is
    unready for anything else (or its answer cannot be read)."""
    try:
        body = json.loads(command(
            "docker", "exec", container, "curl", "-sS", "-m", "5", "http://localhost:8081/readyz",
        ))
    except (subprocess.SubprocessError, ValueError):
        return set()
    unready = body.get("unready") if isinstance(body, dict) else None
    if not isinstance(unready, list) or not unready or not set(unready) <= DEPENDENCY_CHECKS:
        return set()
    return set(unready)


# The dev box reaches api.github.com over a path that sometimes drops a TLS
# connection mid-read (deploy run 37667147156: SSL UNEXPECTED_EOF after the app
# was already released, so the metering image was not). A dropped connection or
# a 5xx is read again; a 4xx, or the same failure three times, still fails the
# job, because a release must not go ahead on validation it could not read.
API_WAITS = (5, 15)


def github_json(url: str) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    for wait in (*API_WAITS, None):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (URLError, ConnectionError, TimeoutError) as error:
            if wait is None or (isinstance(error, HTTPError) and error.code < 500):
                raise
            print(f"Reading {url} failed ({error}); reading it again in {wait} s.")
            time.sleep(wait)
    raise AssertionError("unreachable")


def workflow_runs(repository: str, workflow: str, candidate: str) -> list[dict]:
    """Read every run for one commit without relying on runner-installed gh."""
    runs = []
    page = 1
    while True:
        # No branch filter: a merge-queue run belongs to the queue's temporary
        # branch, not to main. ci_ready checks each run's branch itself.
        query = urlencode({"head_sha": candidate, "per_page": 100, "page": page})
        batch = github_json(
            f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/runs?{query}"
        )["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def ci_ready(candidate: str, say=print) -> bool:
    """Check the latest build and required-CI attempts for this exact main SHA."""
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("the automatic release must name a full commit SHA")
    repository = os.environ["GITHUB_REPOSITORY"]
    ready = True
    # Required CI runs once per commit, in the merge queue. The queue squashes
    # each entry onto its base and fast-forwards main to that same commit, so
    # the queue run's head SHA is the SHA that lands on main.
    for workflow, events, on_branch in (
        ("build.yml", {"push", "workflow_dispatch"}, lambda branch: branch == "main"),
        ("required-ci.yml", {"merge_group"},
         lambda branch: branch.startswith("gh-readonly-queue/main/")),
    ):
        runs = [run for run in workflow_runs(repository, workflow, candidate)
                if run["head_sha"] == candidate and on_branch(run["head_branch"] or "")
                and run["event"] in events
                and run["head_repository"]["full_name"] == repository]
        # A rerun keeps its run ID. Its latest update must supersede an older
        # success, including when another run for the same SHA was created later.
        latest = max(runs, key=lambda run: (run["updated_at"], run["id"]), default=None)
        if (latest is None or any(run["status"] != "completed" for run in runs)
                or latest["conclusion"] != "success"):
            seen = ", ".join(f"{run['id']} {run['status']}/{run['conclusion']}" for run in runs) or "none"
            say(f"Not deploying {candidate}: {workflow} has no successful latest attempt (runs: {seen}).")
            ready = False
    return ready


# GitHub's per-workflow run list can trail the runs themselves: it reported no
# successful build 11-70 s after the build's own completion event said success
# (deploy runs 37441064545, 37612979820 and three more), and the same query
# answered success minutes later. So a refusal is read again for up to two
# minutes before it stands; every caller's job timeout leaves room for that.
SETTLE_SECONDS = 120
SETTLE_INTERVAL = 15


def ci_ready_settled(candidate: str) -> bool:
    deadline = time.monotonic() + SETTLE_SECONDS
    while not ci_ready(candidate):
        if time.monotonic() + SETTLE_INTERVAL > deadline:
            return False
        print(f"Reading {candidate}'s runs again in {SETTLE_INTERVAL} s.")
        time.sleep(SETTLE_INTERVAL)
    return True


def newer_on_main(candidate: str) -> list[str]:
    """The commits on main after the candidate, newest first."""
    repository = os.environ["GITHUB_REPOSITORY"]
    comparison = github_json(f"https://api.github.com/repos/{repository}/compare/{candidate}...main?per_page=100")
    if comparison["status"] not in ("ahead", "identical"):
        raise ValueError(f"candidate {candidate} is not on main: {comparison['status']}")
    return [commit["sha"] for commit in reversed(comparison["commits"])]


# Only one deploy may wait for the deploy-dev group, and a newly waiting one
# cancels the one already waiting, whichever commit each releases. Builds do
# not finish in merge order, so an older commit's late build used to cancel a
# newer commit's waiting deploy and leave main's newest commit off dev until
# the next merge (runs 37646273755 and 37646390290). An older candidate whose
# newer commit can already be released is superseded: its run stops here,
# before it asks for the group, and the newer commit's run releases both.
def superseded_by(candidate: str) -> str:
    for newer in newer_on_main(candidate):
        if ci_ready(newer, say=lambda _line: None):
            return newer
    return ""


def should_skip(candidate: str, rebuilt: bool = False) -> bool:
    """Whether this automatic release must not run. `rebuilt`: the candidate's
    images were just rebuilt under the tag they already had (desktop.yml does
    this to ship new installers), so the box running that tag runs the images
    being replaced, and a healthy box on it is not a duplicate."""
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("the automatic release must name a full commit SHA")
    project = os.environ.get("PROJECT", "cheese")
    repository = os.environ["GITHUB_REPOSITORY"]
    versions = set()
    healthy_services = set()
    running_services = set()
    unhealthy = []
    all_healthy = True
    for service in ("backend", "frontend"):
        # Each runs in one of two slots on a box that releases without downtime
        # (`backend` or `backend-b`, deploy/deploy-docker.sh), so both are read.
        slots = (service, f"{service}-b")
        containers = [
            container
            for slot in slots
            for container in command(
                "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", f"label=com.docker.compose.service={slot}",
            ).split()
        ]
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
            running_services.add(service)
            if health == "healthy":
                healthy_services.add(service)
            else:
                unhealthy.append((service, container))
    if not versions:
        print("No running application containers; allowing bootstrap.")
    all_identical = bool(versions)
    for version in sorted(versions):
        # Read with the same second chance as every other GitHub read here: one
        # dropped TLS read on this comparison failed a whole deploy job (run
        # 37710891132) after the app tier had already been released.
        status = github_json(
            f"https://api.github.com/repos/{repository}/compare/{version}...{candidate}"
        )["status"]
        if status == "behind":
            print(f"Skipping automatic release {candidate}: running release {version} is newer.")
            return True
        if status not in ("ahead", "identical"):
            raise ValueError(f"candidate {candidate} is not a descendant of running release {version}: {status}")
        print(f"Candidate {candidate} is {status} relative to running release {version}.")
        all_identical = all_identical and status == "identical"
    if all_identical and rebuilt:
        print(f"Releasing {candidate} again: its images were rebuilt under the tag it runs.")
        return False
    if all_identical and all_healthy and healthy_services == {"backend", "frontend"}:
        print(f"Skipping duplicate release {candidate}: both application services are healthy.")
        return True
    if all_identical and running_services == {"backend", "frontend"} and unhealthy:
        # The same build is already in place. A backend unready only because a
        # dependency is out of reach is the box's outage, not the build's fault.
        down = set()
        for service, container in unhealthy:
            missing = unready_dependencies(container) if service == "backend" else set()
            if not missing:
                return False
            down |= missing
        print(f"::warning::Skipping release {candidate}: it is already running, and its backend "
              f"is unready only because {', '.join(sorted(down))} is out of reach. Releasing the "
              "same build again cannot bring that back.")
        return True
    return False


def main() -> None:
    if sys.argv[1] == "--require-ci":
        if not ci_ready_settled(sys.argv[2]):
            raise SystemExit("The release commit no longer has successful validation.")
        return
    if sys.argv[1] == "--ci-only":
        ready = ci_ready_settled(sys.argv[2])
        newer = superseded_by(sys.argv[2]) if ready else ""
        if newer:
            print(f"Not deploying {sys.argv[2]}: {newer}, newer on main, has its images and Required CI, "
                  "and its own run releases both.")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"ready={str(ready).lower()}\nsuperseded={newer}\n")
        return
    rebuilt = sys.argv[1] == "--rebuilt"
    candidate = sys.argv[2] if rebuilt else sys.argv[1]
    # CI may have been rerun while this job waited for the deploy runner.
    # That release did not happen, so the job fails: a skip would leave it
    # green, and a green deploy job reads as "this commit is on dev".
    if not ci_ready_settled(candidate):
        raise SystemExit(f"Not deploying {candidate}: its validation is no longer successful.")
    skip = should_skip(candidate, rebuilt=rebuilt)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"skip={str(skip).lower()}\n")


if __name__ == "__main__":
    main()
