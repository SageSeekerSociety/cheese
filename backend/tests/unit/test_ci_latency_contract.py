import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from tests import isolation

ROOT = Path(__file__).resolve().parents[3]
MIRROR = "mirror.gcr.io/"

#: 这些工作流静态门禁共用一段修法：改 .github/workflows 下任一个文件时，别把
#: 「一次 main push 必跑后端与 e2e 套件、lint 独立成 job、镜像走 mirror 且按 digest
#: 固定」这些契约拆掉。契约、托管 runner 与自家 runner 的分工、合并后的行为见
#: docs/manual/dev/ci.md（锚点 #required / #runners / #after-merge）。
_REPAIR_CI = (
    "修法：工作流文件（.github/workflows/*.yml）要保住这条契约——一次 main push "
    "必须真跑后端与 e2e 套件，不设 scope、不让 job 互相 needs 地跳过；lint/静态检查"
    "留在独立的 static job；镜像从 mirror 拉、按 digest 固定。"
    "规则与分工：docs/manual/dev/ci.md。"
    "复查：cd backend && uv run pytest tests/unit/test_ci_latency_contract.py -q"
)


def _rule(failure: str) -> str:
    """一句「哪里破了」，后面跟上共同的修法。"""
    return failure + "\n" + _REPAIR_CI


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
    assert "scope" not in workflow["jobs"], (
        "test.yml 又挂回了 scope job——main 上的绿色必须是套件真跑过，"
        "不是 scope 成功：\n" + _REPAIR_CI
    )
    for name, job in workflow["jobs"].items():
        if name != "test-complete":
            assert "needs" not in job, f"{name} waits on another job\n" + _REPAIR_CI
        assert "scope" not in job.get("if", ""), (
            f"test.yml 的 {name} job 用 if 依赖 scope\n" + _REPAIR_CI
        )


def test_every_main_push_runs_the_e2e_suite():
    workflow = load_workflow("e2e.yml")
    e2e = workflow["jobs"]["e2e"]

    # A pull-request result belongs to the PR head and may have excluded E2E.
    # It is not evidence that the merged main commit ran this workflow.
    assert "scope" not in workflow["jobs"], (
        "e2e.yml 又有了 scope job——PR 上的 E2E 结果不能证明合并后的 main 跑过：\n"
        + _REPAIR_CI
    )
    assert "needs" not in e2e, (
        "e2e job 等别的 job——main push 必须独立跑 E2E：\n" + _REPAIR_CI
    )


def test_remote_execution_keeps_every_main_push():
    concurrency = load_workflow("remote-execution.yml")["concurrency"]

    # A shared main group can cancel either a running job or an older pending
    # job. Each main push therefore needs its own group; PR and experiment runs
    # continue to cancel superseded work.
    assert "github.run_id" in concurrency["group"], (
        "remote-execution.yml 的 concurrency.group 少了 github.run_id——"
        "每次 main push 要有自己的 group，否则新的会取消在跑的：\n" + _REPAIR_CI
    )
    assert "refs/heads/main" in concurrency["group"], (
        "concurrency.group 没把 main 单独分组：\n" + _REPAIR_CI
    )
    assert "refs/heads/main" in concurrency["cancel-in-progress"], (
        "main 上的 concurrency 不再取消被顶掉的工作：\n" + _REPAIR_CI
    )
    assert "github.event_name == 'merge_group'" in concurrency["group"], (
        "concurrency.group 忘了给 merge_group 分组：\n" + _REPAIR_CI
    )
    assert "github.event_name != 'merge_group'" in concurrency["cancel-in-progress"], (
        "merge_group 的取消条件丢了：\n" + _REPAIR_CI
    )


def test_backend_lint_is_a_separate_hosted_job():
    """Lint runs in the `static` job, kept out of `test` (which must not re-run
    ruff/pyright), on a GitHub-hosted runner: the repository is public, hosted
    minutes are free, and nothing in lint needs the pool. `static` also holds
    the other service-free checks, so they share one runner's queue wait. What
    this test guards is the split itself — lint in its own job, never
    duplicated inside `test`."""
    workflow = load_workflow("test.yml")
    lint = workflow["jobs"]["static"]
    assert lint["runs-on"] == "ubuntu-latest", _rule(
        "test.yml 的 static job 不在托管 runner（ubuntu-latest）上跑"
    )

    # The commands themselves moved into .pre-commit-config.yaml, so what is
    # pinned here is that CI goes THROUGH that file rather than restating them —
    # a second copy is how the local gate and CI drifted apart before. Both
    # halves matter: CI must invoke the hook, and the hook must exist.
    lint_commands = "\n".join(s.get("run", "") for s in lint["steps"])
    assert "pre-commit run" in lint_commands, _rule(
        "static job 没有走 pre-commit——lint 命令又散回工作流里了"
    )
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    declared = {h["id"] for repo in config["repos"] for h in repo["hooks"]}
    for hook in ("ruff", "ruff-format", "pyright"):
        assert hook in declared, _rule(
            f"{hook} is not declared in .pre-commit-config.yaml"
        )
        assert "pre-commit run --all-files" in lint_commands, _rule(
            "static job 没跑 pre-commit run --all-files"
        )
        assert hook in lint_commands, _rule(f"static job 没跑 {hook}")

    test_commands = "\n".join(
        s.get("run", "") for s in workflow["jobs"]["test"]["steps"]
    )
    assert "ruff" not in test_commands, _rule(
        "test job 里又跑了 ruff——lint 该留在 static job，不该在 test 里重跑"
    )
    assert "pyright" not in test_commands, _rule(
        "test job 里又跑了 pyright——lint 该留在 static job，不该在 test 里重跑"
    )


def test_merged_checks_are_not_hidden_by_an_earlier_failure():
    static = load_workflow("test.yml")["jobs"]["static"]
    for name in (
        "Fetch main for comparison",
        "Added migrations are safe under the running release",
    ):
        condition = step_named(static, name).get("if", "")
        assert "!cancelled()" in condition, name
        assert "steps.install.outcome == 'success'" in condition, name

    guards = load_workflow("repo-guards.yml")["jobs"]["guards"]
    for name in (
        "Fetch main for comparison",
        "Fetch release tags for the changelog",
        "Build the docs site",
    ):
        assert "!cancelled()" in step_named(guards, name).get("if", ""), name
    node = next(
        s
        for s in guards["steps"]
        if s.get("uses", "").startswith("actions/setup-node@")
    )
    assert "!cancelled()" in node.get("if", "")
    build = step_named(guards, "Build the docs site")
    assert "steps.docs_node.outcome == 'success'" in build["if"]
    assert "steps.docs_tags.outcome == 'success'" in build["if"]


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


def test_image_builds_leave_the_service_host_and_keep_separate_external_caches():
    workflow = load_workflow("build.yml")
    assert workflow["env"]["BUILDKIT_IMAGE"].startswith(MIRROR), _rule(
        "build.yml 的 BUILDKIT_IMAGE 不走 mirror——pool 到不了 Docker Hub"
    )
    assert "@sha256:" in workflow["env"]["BUILDKIT_IMAGE"], _rule(
        "BUILDKIT_IMAGE 没有按 digest 固定"
    )

    scopes = set()
    for name in (
        "build-backend",
        "build-frontend",
        "build-office-render",
        "build-browser-render",
        "build-gateway",
    ):
        job = workflow["jobs"][name]
        assert isinstance(job["runs-on"], str) and job["runs-on"].startswith(
            "ubuntu-"
        ), _rule(f"build.yml 的 {name} job 不在托管 runner 上构建")
        setup = step_named(job, "Set up Docker Buildx")
        assert "BUILDKIT_IMAGE" in setup["with"]["driver-opts"], _rule(
            f"{name} 的 Buildx 没注入 BUILDKIT_IMAGE"
        )
        build = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("docker/build-push-action@")
        )["with"]
        cache_from = dict(part.split("=", 1) for part in build["cache-from"].split(","))
        cache_to = dict(part.split("=", 1) for part in build["cache-to"].split(","))
        assert cache_from["type"] == cache_to["type"] == "gha", _rule(
            f"{name} 的 build cache 类型两面不一致"
        )
        assert cache_from["version"] == cache_to["version"] == "2", _rule(
            f"{name} 的 build cache 版本两面不一致"
        )
        assert cache_from["scope"] == cache_to["scope"], _rule(
            f"{name} 的 build cache scope 两面不一致"
        )
        assert cache_to["scope"] not in scopes, _rule(
            f"{name} 和另一个 job 共用了 cache scope {cache_to['scope']!r}"
        )
        scopes.add(cache_to["scope"])


def test_connector_payload_is_verified_inside_the_backend_image_build():
    workflow = load_workflow("build.yml")
    build_steps = workflow["jobs"]["build-backend"]["steps"]
    assert all(
        step.get("name") != "Verify the image actually carries the connector binaries"
        for step in build_steps
    ), _rule(
        "build.yml 的 build-backend 又在工作流里另外跑一遍连接器二进制校验——"
        "它已经在 backend/Dockerfile 里了"
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
    costs nothing. And build toolchains have no runtime caller, so a production
    stage that inherits one ships what nothing runs.
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

    assert "--max-used-space 6GB" in prune, _rule(
        "deploy/deploy-docker.sh 的 builder prune 不再设 --max-used-space 6GB 上限"
    )
    assert "--min-free-space 10GB" in deploy, _rule(
        "deploy/deploy-docker.sh 不再保 --min-free-space 10GB"
    )
    assert "docker builder prune -af >/dev/null" not in deploy, _rule(
        "deploy-docker.sh 又用了无差别的 prune -af——每次构建都会变冷"
    )


def test_a_layer_can_hit_its_own_ceiling_before_the_job_hits_its_own():
    """A runner must preserve time to report a timed-out partition's failure."""
    test_job = load_workflow("test.yml")["jobs"]["test"]
    step = step_named(test_job, "Run the selected layer partition")
    assert step["timeout-minutes"] == "${{ matrix.timeout }}", _rule(
        "test.yml 的分区步骤没有按 matrix.timeout 设自己的超时——"
        "runner 要留时间上报超时那个分区的失败"
    )
    for partition in test_job["strategy"]["matrix"]["include"]:
        assert partition["timeout"] < test_job["timeout-minutes"], _rule(
            f"分区 {partition.get('layer', partition)!r} 的超时不小于整个 job 的超时"
        )


def test_e2e_service_ports_stay_outside_the_pool_ephemeral_range():
    """Long-lived listeners must not compete with outbound client sockets.

    All three Linux pool machines use 32768-60999 for ephemeral ports. Execute
    the workflow's real scoping step for both configured runner slots and keep
    every fixed service listener outside that range.
    """
    step = step_named(
        load_workflow("e2e.yml")["jobs"]["e2e"], "Scope this run to its runner slot"
    )
    observed: list[dict[str, str]] = []
    for slot in ("0", "1"):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "github-env"
            env = {
                **os.environ,
                "CHEESE_CI_SLOT": slot,
                "GITHUB_ENV": str(output),
                "GITHUB_RUN_ID": "fixture-run",
                "GITHUB_RUN_ATTEMPT": "1",
                "RUNNER_TEMP": temp,
            }
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", step["run"]], env=env, check=True
            )
            observed.append(
                dict(line.split("=", 1) for line in output.read_text().splitlines())
            )

    ephemeral = range(32768, 61000)
    listeners = (
        "E2E_BACKEND_PORT",
        "E2E_FRONTEND_PORT",
        "E2E_STUB_GATEWAY_PORT",
        "FORGEJO_TEST_PORT",
        "FORGEJO_TEST_S3_PORT",
    )
    for scoped in observed:
        for name in listeners:
            assert int(scoped[name]) not in ephemeral, (name, scoped[name])
    for name in listeners:
        assert observed[0][name] != observed[1][name], name


def test_a_scope_job_names_the_failure_and_the_repair(monkeypatch):
    """workflow 里重新长出 scope job 时，报出原因和修法。用 monkeypatch 喂一个假的
    workflow，触发守卫真正会红的分支。"""
    monkeypatch.setattr(
        "tests.unit.test_ci_latency_contract.load_workflow",
        lambda name: {"jobs": {"scope": {}, "test": {}}},
    )
    with pytest.raises(AssertionError) as caught:
        test_every_main_push_runs_the_backend_suite()
    message = str(caught.value)
    assert "scope" in message
    assert "docs/manual/dev/ci.md" in message
    assert "test_ci_latency_contract.py" in message


def test_a_partition_timeout_over_the_job_is_named(monkeypatch):
    """分区超时 >= job 超时时，报出该分区和修法。"""
    workflow = {
        "jobs": {
            "test": {
                "timeout-minutes": 30,
                "strategy": {"matrix": {"include": [{"timeout": 40, "layer": "pure"}]}},
                "steps": [
                    {
                        "name": "Run the selected layer partition",
                        "timeout-minutes": "${{ matrix.timeout }}",
                    }
                ],
            }
        }
    }
    monkeypatch.setattr(
        "tests.unit.test_ci_latency_contract.load_workflow", lambda name: workflow
    )
    with pytest.raises(AssertionError) as caught:
        test_a_layer_can_hit_its_own_ceiling_before_the_job_hits_its_own()
    message = str(caught.value)
    assert "pure" in message
    assert "docs/manual/dev/ci.md" in message
