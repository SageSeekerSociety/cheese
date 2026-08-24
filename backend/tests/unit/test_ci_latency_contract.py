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
        # The saving this test exists for: a commit that already passed CI on
        # its PR must not re-run the suites after the squash merge. That is the
        # `!= 'false'` — an explicit `false` from scope still skips.
        assert "needs.scope.outputs.run_heavy != 'false'" in heavy["if"]
        # …but the gate must not be the veto form it used to be. Written as
        # `== 'true'`, an EMPTY output (scope failed, was cancelled, or never
        # started) also skipped the heavy job, so "we could not decide" and "we
        # decided to skip" were the same condition. On 2026-08-13 the org's
        # Actions billing lapsed, every hosted job was refused before its first
        # step, and `scope` — hosted on purpose, it is two `gh api` calls — took
        # the self-hosted test suite down with it on machines that were idle.
        # The workflow then reported nothing failed, having tested nothing.
        assert "run_heavy == 'true'" not in heavy["if"], (
            "the gate is back to its veto form: an optimisation that cannot "
            "decide must run the tests, not skip them"
        )
        # And it must still stop for a superseding push — `always()` here would
        # trade one wasted-CI bug for another, since PRs use cancel-in-progress.
        assert "cancelled()" in heavy["if"]


def test_backend_lint_is_a_separate_hosted_job():
    """Lint is its own job, kept out of `test` (which must not re-run
    ruff/pyright). It used to run on GitHub-hosted `ubuntu-latest` (#166) to
    stay fast and off the self-hosted pool — until 2026-08-13, when the org's
    Actions billing lapsed and every hosted job was refused before its first
    step. The whole merge gate now runs on the self-hosted `cheese-ci` pool
    (#383) so CI no longer depends on GitHub's paid minutes; the ~1min
    pool-queue latency is the deliberate price. What this test still guards is
    the split itself — a separate lint job, never duplicated inside `test`."""
    workflow = load_workflow("test.yml")
    lint = workflow["jobs"]["lint"]
    assert lint["runs-on"] == ["self-hosted", "cheese-ci"]
    # Gated by `scope` like the heavy job, and by the same non-veto rule: a
    # scope that could not decide must let lint run, not silently skip it. The
    # test above pins this for `test`; without it here, lint could be reverted
    # to the veto form on its own and nothing would say so.
    assert "run_heavy == 'true'" not in lint["if"]
    assert "needs.scope.outputs.run_heavy != 'false'" in lint["if"]

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
