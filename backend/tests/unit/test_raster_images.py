"""Which files are pictures (`app/core/raster_images.py`): the one answer the
avatars, attachments, the model's image input and the machine's files share."""

import runpy
import shutil
from pathlib import Path

import pytest

from app.core import raster_images
from app.domain.agent.harness.claude_code.remote_execution import (
    machine_files,
    runtime,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 8
GIF = b"GIF89a" + b"\0" * 8
WEBP = b"RIFF\x24\0\0\0WEBPVP8 "
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'


@pytest.mark.parametrize(
    ("head", "media_type"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (b"GIF87a" + b"\0" * 8, "image/gif"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
        (SVG, None),
        (b"%PDF-1.7", None),
        (b"", None),
    ],
)
def test_a_file_is_known_by_its_first_bytes(head, media_type):
    assert raster_images.sniff(head) == media_type


def test_svg_is_never_a_raster_image():
    assert not raster_images.is_raster("image/svg+xml")
    assert ".svg" not in raster_images.SUFFIX_MEDIA_TYPE
    assert all(raster_images.is_raster(t) for t in raster_images.MEDIA_TYPE_SUFFIX)


def test_every_raster_suffix_and_media_type_name_each_other():
    for suffix, media_type in raster_images.SUFFIX_MEDIA_TYPE.items():
        saved_as = raster_images.MEDIA_TYPE_SUFFIX[media_type]
        assert raster_images.SUFFIX_MEDIA_TYPE[saved_as] == media_type
        assert suffix == suffix.lower()


def test_the_machine_reads_images_by_the_same_rule(tmp_path):
    for name, data in (("a.png", PNG), ("b.webp", WEBP), ("c.svg", SVG)):
        (tmp_path / name).write_bytes(data)
    assert machine_files.open_file(str(tmp_path / "a.png"), False)["type"] == (
        "image/png"
    )
    assert machine_files.open_file(str(tmp_path / "b.webp"), False)["type"] == (
        "image/webp"
    )
    assert machine_files.open_file(str(tmp_path / "c.svg"), False)["type"] is None


def test_a_release_carries_the_rule_beside_the_machines_files(tmp_path):
    """On a machine there is no backend to read it from: the release ships it
    beside `machine_files.py`, and that copy is the one used."""
    backend = Path(runtime.__file__).resolve().parents[6]
    shipped = {
        name: source
        for name, source in runtime.RELEASE_FILES.items()
        if name
        in ("remote-execution/machine_files.py", "remote-execution/raster_images.py")
    }
    assert len(shipped) == 2
    for name, source in shipped.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(backend / source, tmp_path / name)
    (tmp_path / "remote-execution/a.gif").write_bytes(GIF)
    shipped_files = runpy.run_path(str(tmp_path / "remote-execution/machine_files.py"))
    opened = shipped_files["open_file"](str(tmp_path / "remote-execution/a.gif"), False)
    assert opened["type"] == "image/gif"
    loaded = shipped_files["_raster_images"]()
    assert loaded["__file__"] == str(tmp_path / "remote-execution/raster_images.py")
