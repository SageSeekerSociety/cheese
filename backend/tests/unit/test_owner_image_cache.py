"""The released owner image is reused only for the pinned Linux platform."""

import io
import json

import pytest

from scripts import device_connection_lifecycle_acceptance as lifecycle

IMAGE = "ghcr.io/example/owner@sha256:" + "a" * 64


class Docker:
    def __init__(self, platform: str, pulled_platform: str = "linux/amd64"):
        self.platform = platform
        self.pulled_platform = pulled_platform
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ("image", "inspect"):
            assert args[-1] == IMAGE
            assert kwargs == {"check": False}
            return self.platform
        if args[0] == "pull":
            self.platform = self.pulled_platform
        return ""


def events(log):
    return [json.loads(line) for line in log.getvalue().splitlines()]


def test_cached_exact_digest_and_platform_needs_no_registry(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "private-token")
    docker = Docker("linux/amd64")
    log = io.StringIO()

    lifecycle.prepare_owner_image(IMAGE, docker, log)

    assert [args[0] for args, _ in docker.calls] == ["image"]
    assert [
        (row["event"], row["owner_image"], row["platform"]) for row in events(log)
    ] == [("image_reused", IMAGE, "linux/amd64")]
    assert "private-token" not in log.getvalue()


@pytest.mark.parametrize("local_platform", ["", "linux/arm64"])
def test_uncached_or_wrong_platform_logs_in_and_pulls(monkeypatch, local_platform):
    monkeypatch.setenv("GH_TOKEN", "private-token")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "example")
    docker = Docker(local_platform)
    log = io.StringIO()

    lifecycle.prepare_owner_image(IMAGE, docker, log)

    assert [args[0] for args, _ in docker.calls] == ["image", "login", "pull", "image"]
    assert docker.calls[1] == (
        ("login", "ghcr.io", "-u", "example", "--password-stdin"),
        {"input": "private-token"},
    )
    assert docker.calls[2] == (
        ("pull", "--platform", "linux/amd64", IMAGE),
        {"timeout": 300},
    )
    assert [row["event"] for row in events(log)] == [
        "image_pull_started",
        "image_pull_finished",
    ]
    assert "private-token" not in log.getvalue()


def test_wrong_platform_after_pull_is_not_accepted(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "private-token")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "example")
    docker = Docker("", "linux/arm64")
    log = io.StringIO()

    with pytest.raises(RuntimeError, match="expected linux/amd64"):
        lifecycle.prepare_owner_image(IMAGE, docker, log)

    assert [row["event"] for row in events(log)] == ["image_pull_started"]
