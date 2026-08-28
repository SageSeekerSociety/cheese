from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_normal_build_publishes_the_runtime_image():
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    sandbox_steps = workflow["jobs"]["build-sandbox"]["steps"]
    names = {step.get("name") for step in sandbox_steps}

    assert "Build and push sandbox image" in names


def test_backend_receives_sha_pinned_runtime_images():
    compose = yaml.safe_load(
        (ROOT / "deploy/compose/docker-compose.base.yml").read_text()
    )
    environment = compose["services"]["backend"]["environment"]

    assert (
        "SANDBOX_IMAGE=${SANDBOX_IMAGE:-ghcr.io/sageseekersociety/cheese/"
        "sandbox:${IMAGE_TAG:-main}}"
    ) in environment
    assert (
        "QUALITY_GATE_IMAGE=${QUALITY_GATE_IMAGE:-ghcr.io/"
        "sageseekersociety/cheese/sandbox:${IMAGE_TAG:-main}}"
    ) in environment


def test_gateway_healthcheck_only_uses_tools_in_the_gateway_image():
    compose = yaml.safe_load(
        (ROOT / "deploy/compose/docker-compose.gateway.yml").read_text()
    )
    probe = compose["services"]["litellm"]["healthcheck"]["test"]

    assert probe[:3] == ["CMD", "/app/.venv/bin/python", "-c"]
    assert "urllib.request.urlopen" in probe[3]
    assert "curl" not in probe
