"""File versions identify content rather than write time."""

from app.domain.workspace.textfile import content_version


def test_version_identifies_content_not_the_moment_it_was_written():
    assert content_version(b"abc") == content_version(b"abc")
    assert content_version(b"abc") != content_version(b"abd")
