"""Root backups reject incompatible images and unsafe or oversized archives."""

import gzip
import io
import tarfile

import pytest

from app.domain.agent import rootfs_snapshot as roots


def image(number):
    return "ghcr.io/example/root@sha256:" + f"{number:064x}"


def test_an_image_update_leaves_the_new_system_untouched(tmp_path):
    result = roots.restore(
        tmp_path, image(2), {"image": image(1)}, bwrap="unused", namespace=[]
    )
    assert result == {"status": "image_changed"}
    assert not roots.layer_for(tmp_path, image(2)).exists()


def test_a_home_keeps_its_local_installs_when_a_backup_is_offered(tmp_path):
    layer = roots.layer_for(tmp_path, image(1))
    upper = layer / "upper"
    upper.mkdir(parents=True)
    (upper / "recent-install").write_text("local change")
    result = roots.restore(
        tmp_path, image(1), {"image": image(1)}, bwrap="unused", namespace=[]
    )
    assert result == {"status": "existing"}
    assert (upper / "recent-install").read_text() == "local change"


@pytest.mark.parametrize("name", ["../../escape", "/absolute"])
def test_an_archive_cannot_name_a_file_outside_its_upper(tmp_path, name):
    archive = tmp_path / "upper.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        member = tarfile.TarInfo(name)
        member.size = 1
        out.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="escaping path"):
        roots.inspect_archive(archive)


def test_a_small_compressed_archive_cannot_exceed_the_unpacked_limit(tmp_path):
    archive = tmp_path / "upper.tar.gz"
    member = tarfile.TarInfo("huge")
    member.size = roots.EXPANDED_MAX + 1
    with gzip.open(archive, "wb") as out:
        out.write(member.tobuf())
    with pytest.raises(roots.TooLarge):
        roots.inspect_archive(archive)


def test_a_corrupt_download_publishes_no_partial_root(tmp_path):
    source = tmp_path / "corrupt.gz"
    source.write_bytes(b"broken backup")
    snapshot = {"image": image(1), "url": source.as_uri(), "size": 13, "md5": "0" * 32}
    with pytest.raises(ValueError, match="checksum mismatch"):
        roots.restore(tmp_path, image(1), snapshot, bwrap="unused", namespace=[])
    assert not roots.layer_for(tmp_path, image(1)).exists()
