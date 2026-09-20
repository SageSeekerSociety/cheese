import re
from pathlib import Path

import yaml

from tests import isolation

ROOT = Path(__file__).resolve().parents[3]
MIRROR = "mirror.gcr.io/"


def load_workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def step_named(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_every_main_push_runs_the_backend_suite():
    """A squash merge lands on a main that has moved since the PR was tested, so
    two individually green PRs can be red together. The `scope` job that once
    skipped `test` on main when the squash tree had passed on its PR is gone:
    its premise only held when the PR was up to date with main, which at thirty
    merges a day it rarely was, and main's green was the scope job succeeding,
    not the suite. Hosted runners make the full run affordable."""
    workflow = load_workflow("test.yml")
    assert "scope" not in workflow["jobs"]
    for name, job in workflow["jobs"].items():
        assert "needs" not in job, f"{name} waits on another job"
        assert "scope" not in job.get("if", ""), name


def test_backend_lint_is_a_separate_hosted_job():
    """Lint is its own job, kept out of `test` (which must not re-run
    ruff/pyright), and it runs on a GitHub-hosted runner: the repository is
    public, hosted minutes are free, and nothing in lint needs the pool. What
    this test guards is the split itself — a separate lint job, never
    duplicated inside `test`."""
    workflow = load_workflow("test.yml")
    lint = workflow["jobs"]["lint"]
    assert lint["runs-on"] == "ubuntu-latest"

    # The commands themselves moved into .pre-commit-config.yaml, so what is
    # pinned here is that CI goes THROUGH that file rather than restating them —
    # a second copy is how the local gate and CI drifted apart before. Both
    # halves matter: CI must invoke the hook, and the hook must exist.
    lint_commands = "\n".join(s.get("run", "") for s in lint["steps"])
    assert "pre-commit run" in lint_commands
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    declared = {h["id"] for repo in config["repos"] for h in repo["hooks"]}
    for hook in ("ruff", "ruff-format", "pyright"):
        assert hook in declared, f"{hook} is not declared in .pre-commit-config.yaml"
        assert "pre-commit run --all-files" in lint_commands
        assert hook in lint_commands

    test_commands = "\n".join(
        s.get("run", "") for s in workflow["jobs"]["test"]["steps"]
    )
    assert "ruff" not in test_commands
    assert "pyright" not in test_commands


def test_ci_service_images_do_not_depend_on_docker_hub():
    """The pool cannot reach Docker Hub — auth.docker.io closes the connection —
    so every image a CI machine pulls comes from the mirror, pinned by digest.

    On the pool they live in `resident-services.sh`: one Postgres and one Valkey
    per MACHINE, because a job's own would bind host 5432/6379 and bring a 3 GB
    tmpfs each, which is what kept a machine to one job.
    """
    script = (ROOT / "deploy/ci-runner/resident-services.sh").read_text()
    images = re.findall(r'^[A-Z_]*IMAGE="([^"]+)"', script, re.MULTILINE)
    assert len(images) == 2, images
    for image in images:
        assert image.startswith(MIRROR), image
        assert "@sha256:" in image, image

    # A hosted job brings its own pair as service containers; they must be the
    # same mirror-hosted, digest-pinned images the pool runs, so the test
    # settings do not depend on which kind of runner they land on.
    for filename, job_name in (("test.yml", "test"), ("e2e.yml", "e2e")):
        job = load_workflow(filename)["jobs"][job_name]
        for service in job.get("services", {}).values():
            assert service["image"].startswith(MIRROR), service["image"]
            assert "@sha256:" in service["image"], service["image"]
            assert service["image"] in images, service["image"]


def test_the_resident_valkey_has_room_for_every_slot():
    """Each runner slot takes its own block of Redis databases so two runs on one
    machine cannot share an index. Valkey ships 16; one block is all of them."""
    script = (ROOT / "deploy/ci-runner/resident-services.sh").read_text()
    default = re.search(
        r'VALKEY_DATABASES="\$\{CHEESE_CI_VALKEY_DATABASES:-(\d+)\}"', script
    )
    assert default, script
    assert int(default[1]) >= 2 * isolation.REDIS_DATABASES_PER_SLOT, default[1]


def test_buildkit_uses_the_mirror_and_keeps_cache_on_the_persistent_runner():
    workflow = load_workflow("build.yml")
    assert workflow["env"]["BUILDKIT_IMAGE"].startswith(MIRROR)
    assert "@sha256:" in workflow["env"]["BUILDKIT_IMAGE"]

    for name in ("build-backend", "build-sandbox", "build-frontend"):
        job = workflow["jobs"][name]
        setup = step_named(job, "Set up Docker Buildx")
        retain = step_named(job, "Retain the BuildKit image")
        reclaim = step_named(job, "Reclaim build cache")

        assert setup["with"]["keep-state"] is True
        assert "mirror.gcr.io" in setup["with"]["buildkitd-config-inline"]
        assert "BUILDKIT_IMAGE" in setup["with"]["driver-opts"]
        assert "cheese-buildkit-image-retainer" in retain["run"]
        assert "docker buildx prune" in reclaim["run"]
        assert "--max-used-space 20GB" in reclaim["run"]
        assert "--min-free-space 10GB" in reclaim["run"]


def test_connector_payload_is_verified_inside_the_backend_image_build():
    workflow = load_workflow("build.yml")
    build_steps = workflow["jobs"]["build-backend"]["steps"]
    assert all(
        step.get("name") != "Verify the image actually carries the connector binaries"
        for step in build_steps
    )

    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    assert "--from=connector" in dockerfile
    assert "./connector-dist" in dockerfile
    assert 'test -s "$f"' in dockerfile


def _production_stage_lineage() -> str:
    """Every instruction the production image is actually built from, following
    the FROM chain back to its root."""
    stages: dict[str, tuple[str, list[str]]] = {}
    current: list[str] = []
    for line in (ROOT / "backend" / "Dockerfile").read_text().splitlines():
        # A comment often explains what a stage deliberately does NOT do, so
        # reading comments as instructions makes each such note trip the check.
        if line.strip().startswith("#"):
            continue
        words = line.split()
        if words[:1] == ["FROM"]:
            current = []
            stages[words[-1]] = (words[1], current)
        else:
            current.append(line)

    lineage: list[str] = []
    cursor = "production"
    while cursor in stages:
        parent, body = stages[cursor]
        lineage += body
        cursor = parent
    return "\n".join(lineage)


def test_a_backend_commit_does_not_rewrite_the_whole_image():
    """The production image pays for its layers twice per commit unless two
    things hold, and neither is visible from the outside.

    `chown -R` over a populated /app copies up every file it touches — on
    2026-08-17 that single instruction ran for 101s and left a near-duplicate of
    .venv + connector-dist to export (51s) and push (70s), on every commit,
    because it sits below `COPY app`. Setting ownership as each COPY writes
    costs nothing. And the compilers that build srp_rs have no runtime caller,
    so a production stage that inherits them ships ~1.5GB nothing runs.
    """
    lineage = _production_stage_lineage()

    assert "chown -R" not in lineage, (
        "production is recursively chowning a tree again — use COPY --chown, "
        "which sets the owner as the layer is written"
    )
    for toolchain in ("rustup", "build-essential"):
        assert toolchain not in lineage, (
            f"{toolchain} is back in the production image's lineage; nothing at "
            "runtime calls it"
        )


def test_deploy_bounds_cache_without_making_every_build_cold():
    deploy = (ROOT / "deploy" / "deploy-docker.sh").read_text()
    prune = next(line for line in deploy.splitlines() if "docker builder prune" in line)

    assert "--max-used-space 6GB" in prune
    assert "--min-free-space 10GB" in deploy
    assert "docker builder prune -af >/dev/null" not in deploy
