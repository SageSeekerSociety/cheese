import os
import subprocess
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


def test_center_host_fuse_runtime_is_installed_by_normal_deploy():
    deploy = (ROOT / "deploy/deploy-docker.sh").read_text()
    assert 'CHEESE_CENTRAL_SESSION_HOST:-}" = "1"' in deploy
    assert ". /etc/os-release" in deploy
    assert "$VERSION_CODENAME main" in deploy
    assert "mirrors.tuna.tsinghua.edu.cn/debian" in deploy
    assert "signed-by=/usr/share/keyrings/debian-archive-keyring.gpg" in deploy
    assert "Dir::Etc::sourcelist=$fuse_apt_source" in deploy
    assert "Dir::Etc::sourceparts=-" in deploy
    assert 'sudo apt-get "${fuse_apt_options[@]}" update -qq' in deploy
    assert (
        'sudo apt-get "${fuse_apt_options[@]}" install -y -qq fuse libfuse2' in deploy
    )
    assert "trap 'rm -f \"$fuse_apt_source\"' EXIT" in deploy
    assert "libfuse.so.2 " in deploy
    assert "/dev/fuse" not in deploy


def test_fuse_install_uses_scoped_source_only_on_center_host(tmp_path):
    deploy = (ROOT / "deploy/deploy-docker.sh").read_text()
    start = deploy.index('if [ "${CHEESE_CENTRAL_SESSION_HOST:-}" = "1" ]')
    end = deploy.index("\nfi", start) + len("\nfi")
    block = deploy[start:end].replace(
        ". /etc/os-release", "VERSION_CODENAME=test-codename"
    )
    calls = tmp_path / "sudo-calls"
    source_path = tmp_path / "source-path"
    harness = f"""
log() {{ :; }}
fail() {{ echo "$*" >&2; exit 1; }}
command() {{ return 1; }}
ldconfig() {{ :; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls}
  for argument in "$@"; do
    case "$argument" in
      Dir::Etc::sourcelist=*)
        path="${{argument#*=}}"
        printf '%s' "$path" > {source_path}
        cat "$path" >> {calls}
        ;;
    esac
  done
}}
{block}
"""

    environment = dict(os.environ, CHEESE_CENTRAL_SESSION_HOST="0")
    subprocess.run(["bash", "-c", harness], check=True, env=environment)
    assert not calls.exists()

    environment["CHEESE_CENTRAL_SESSION_HOST"] = "1"
    subprocess.run(["bash", "-c", harness], check=True, env=environment)
    output = calls.read_text()
    assert output.count("apt-get") == 2
    assert " test-codename main" in output
    assert "mirrors.tuna.tsinghua.edu.cn/debian" in output
    assert "signed-by=/usr/share/keyrings/debian-archive-keyring.gpg" in output
    assert not Path(source_path.read_text()).exists()
