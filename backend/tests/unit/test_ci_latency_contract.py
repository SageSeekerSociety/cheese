from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MIRROR = "mirror.gcr.io/"


def load_workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def step_named(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_squash_merge_does_not_repeat_pr_backend_and_e2e_suites():
    for filename, heavy_job_name in (("test.yml", "test"), ("e2e.yml", "e2e")):
        workflow = load_workflow(filename)
        scope = workflow["jobs"]["scope"]
        heavy = workflow["jobs"][heavy_job_name]
        decision = step_named(scope, "Decide whether this commit already passed PR CI")

        assert "run_heavy" in scope["outputs"]
        assert "commits/$GITHUB_SHA/pulls" in decision["run"]
        assert "actions/workflows/$WORKFLOW_FILE/runs" in decision["run"]
        assert "| jq" not in decision["run"]
        assert "--jq" in decision["run"]
        assert scope["permissions"]["actions"] == "read"
        assert heavy["needs"] == "scope"
        assert "needs.scope.outputs.run_heavy == 'true'" in heavy["if"]


def test_backend_lint_reuses_the_test_environment():
    workflow = load_workflow("test.yml")
    assert "lint" not in workflow["jobs"]

    test_steps = workflow["jobs"]["test"]["steps"]
    commands = "\n".join(step.get("run", "") for step in test_steps)
    assert "ruff format --check ." in commands
    assert "ruff check ." in commands


def test_ci_service_images_do_not_depend_on_docker_hub():
    for filename, job_name in (("test.yml", "test"), ("e2e.yml", "e2e")):
        services = load_workflow(filename)["jobs"][job_name]["services"]
        for service in services.values():
            assert service["image"].startswith(MIRROR)
            assert "@sha256:" in service["image"]


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
        assert "--max-used-space 6GB" in reclaim["run"]
        assert "--min-free-space 10GB" in reclaim["run"]


def test_connector_payload_is_verified_inside_the_backend_image_build():
    workflow = load_workflow("build.yml")
    build_steps = workflow["jobs"]["build-backend"]["steps"]
    assert all(
        step.get("name") != "Verify the image actually carries the connector binaries"
        for step in build_steps
    )

    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    assert "COPY --from=connector /out ./connector-dist" in dockerfile
    assert 'test -s "$f"' in dockerfile


def test_deploy_bounds_cache_without_making_every_build_cold():
    deploy = (ROOT / "deploy" / "deploy-docker.sh").read_text()
    prune = next(line for line in deploy.splitlines() if "docker builder prune" in line)

    assert "--max-used-space 6GB" in prune
    assert "--min-free-space 10GB" in deploy
    assert "docker builder prune -af >/dev/null" not in deploy
