import hashlib

from app.domain.agent.harness.claude_code.remote_execution import launch, runtime


def test_a_host_root_executor_can_prepare_with_an_empty_image_setting():
    info = {
        "sandbox": True,
        "sandbox_image": None,
        "capabilities": ["prepare"],
        "protocol_version": runtime.PROTOCOL_VERSION,
        "files": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in launch.file_sources().items()
        },
    }
    assert launch.can_prepare(info, True, image="")
    info["sandbox_image"] = "ghcr.io/example/sandbox@sha256:" + "f" * 64
    assert not launch.can_prepare(info, True, image=None)


def test_a_cloud_launch_does_not_assume_an_unprepared_default_image():
    payload = launch.payload_for(
        "project", "resource", {}, sandbox=True, platform_machine=True
    )
    assert payload["sandbox_image"] is None


def test_a_launch_uses_the_ready_image_selected_for_its_sandbox():
    image = "ghcr.io/example/sandbox@sha256:" + "f" * 64
    payload = launch.payload_for(
        "project",
        "resource",
        {"CHEESE_SANDBOX_IMAGE": image},
        sandbox=True,
        platform_machine=True,
    )
    assert payload["sandbox_image"] == image


def test_an_enrolled_host_keeps_accepting_while_the_default_image_changes():
    from app.domain.machine.models import CloudHost
    from app.domain.machine.reaper import accepting

    host = CloudHost(
        cores=4,
        memory_mb=8192,
        disk_gb=40,
        device_id="test-device",
        sandbox_image="ghcr.io/example/sandbox@sha256:" + "a" * 64,
    )
    assert accepting(host)
