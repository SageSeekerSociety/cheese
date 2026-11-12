"""Cloud images are prepared once, and remain readable while a sandbox uses them."""

import os
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from app.domain.agent import sandbox_image


def reference(number):
    return "ghcr.io/example/sandbox@sha256:" + f"{number:064x}"


@pytest.fixture
def producer(tmp_path):
    source = tmp_path / "file"
    source.write_text("from the image")
    archive = tmp_path / "filesystem.tar"
    with tarfile.open(archive, "w") as out:
        out.add(source, arcname="file")
    program = tmp_path / "export"
    program.write_text(
        f"#!/bin/sh\nfor output; do :; done\ncp '{archive}' \"$output\"\n"
    )
    program.chmod(0o755)
    return program


def test_a_prepared_image_can_start_after_its_source_is_offline(tmp_path, producer):
    cache = tmp_path / "cache"
    sandbox_image.prewarm(reference(1), cache=cache, crane=str(producer))
    producer.unlink()
    sandbox_image.prewarm(reference(1), cache=cache, crane=str(producer))
    with sandbox_image.lease(reference(1), cache=cache) as (root, _):
        assert (root / "file").read_text() == "from the image"


def test_enrollment_umask_does_not_make_the_image_unreadable(tmp_path, producer):
    cache = tmp_path / "cache"
    previous = os.umask(0o077)
    try:
        sandbox_image.prewarm(reference(1), cache=cache, crane=str(producer))
    finally:
        os.umask(previous)
    with sandbox_image.lease(reference(1), cache=cache) as (root, descriptor):
        assert root.stat().st_mode & 0o005 == 0o005
        assert os.fstat(descriptor).st_mode & 0o004
        assert (root / "file").read_text() == "from the image"


def test_pruning_waits_for_a_running_sandbox_to_release_its_image(tmp_path, producer):
    cache = tmp_path / "cache"
    sandbox_image.prewarm(reference(1), cache=cache, crane=str(producer))
    with sandbox_image.lease(reference(1), cache=cache) as (root, _):
        sandbox_image.prewarm(reference(2), keep=0, cache=cache, crane=str(producer))
        assert (root / "file").read_text() == "from the image"
    sandbox_image.prewarm(reference(2), keep=0, cache=cache, crane=str(producer))
    assert not root.exists()


def test_an_uncached_image_is_refused_without_a_pull(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ".lock").touch()
    with pytest.raises(RuntimeError, match="not prewarmed"):
        with sandbox_image.lease(reference(1), cache=cache):
            pytest.fail("an uncached rootfs was admitted")


def test_a_prewarmed_sandbox_does_not_wait_for_another_image_download(
    tmp_path, producer
):
    cache = tmp_path / "cache"
    sandbox_image.prewarm(reference(1), cache=cache, crane=str(producer))
    waiting, release = tmp_path / "waiting", tmp_path / "release"
    program = producer.read_text()
    producer.write_text(
        f"#!/bin/sh\ntouch '{waiting}'\nwhile [ ! -f '{release}' ]; do sleep 0.01; done\n"
        + program.split("\n", 1)[1]
    )

    def admit():
        with sandbox_image.lease(reference(1), cache=cache) as (root, _):
            return (root / "file").read_text()

    with ThreadPoolExecutor(max_workers=2) as workers:
        downloading = workers.submit(
            sandbox_image.prewarm, reference(2), cache=cache, crane=str(producer)
        )
        deadline = time.monotonic() + 2
        while not waiting.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        admitted = workers.submit(admit)
        try:
            try:
                content = admitted.result(timeout=1)
            except TimeoutError:
                content = None
        finally:
            release.touch()
        downloading.result(timeout=2)
    assert content == "from the image", (
        "a cached sandbox waited for a different image's pull"
    )


@pytest.mark.parametrize(
    "value",
    ["ghcr.io/example/sandbox:latest", "docker.io/example/sandbox@sha256:" + "0" * 64],
)
def test_only_digest_pinned_registry_images_are_admitted(value):
    with pytest.raises(ValueError, match="digest-pinned"):
        sandbox_image.image_key(value)
