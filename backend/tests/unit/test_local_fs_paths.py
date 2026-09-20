"""Path normalization and containment — the properties the guarantee rests on.

These are written against what a filesystem *does*, not against the module's
internals: each case names a path a person could type (or an attacker could send)
and the answer the disk would give. If the implementation and these disagree, the
implementation is wrong, which is why none of them was derived by reading it.
"""

import pytest

from app.domain.local_fs.paths import (
    PathRefused,
    Platform,
    contains,
    normalize,
)

BS = chr(92)


@pytest.mark.parametrize(
    ("raw", "platform", "expected"),
    [
        # One path, one spelling — whichever separator the caller used.
        ("C:/Users/alice/MyDocs", Platform.WINDOWS, "C:/Users/alice/MyDocs"),
        ("C:" + BS + "Users" + BS + "alice" + BS + "MyDocs", Platform.WINDOWS,
         "C:/Users/alice/MyDocs"),
        # The canonical text keeps the caller's case except for the drive
        # letter; it is the comparison KEY that folds (asserted below).
        ("c:/users/alice/mydocs", Platform.WINDOWS, "C:/users/alice/mydocs"),
        ("/home/alice/docs", Platform.LINUX, "/home/alice/docs"),
        ("/Users/alice/Docs", Platform.MACOS, "/Users/alice/Docs"),
        # Dots collapse; a trailing slash is not a different directory.
        ("/home/alice/docs/./x/../y", Platform.LINUX, "/home/alice/docs/y"),
        ("/home/alice/docs/", Platform.LINUX, "/home/alice/docs"),
        # Win32 strips a trailing dot or blank from a segment before opening it,
        # so the normalized path has to strip it too or the two disagree.
        ("C:/Users/alice/MyDocs./x.txt", Platform.WINDOWS,
         "C:/Users/alice/MyDocs/x.txt"),
        # A UNC share is a root, and the root is kept.
        ("//server/share/dir/a.txt", Platform.WINDOWS,
         "//server/share/dir/a.txt"),
    ],
)
def test_normalize_collapses_to_one_canonical_form(raw, platform, expected):
    assert normalize(raw, platform).text == expected


@pytest.mark.parametrize(
    ("raw", "platform", "reason"),
    [
        # A relative path names whatever directory the process happens to be in.
        # It cannot be authorized, because the directory it denotes is not fixed.
        ("Documents", Platform.LINUX, "not_absolute"),
        ("./docs", Platform.LINUX, "not_absolute"),
        ("../docs", Platform.LINUX, "not_absolute"),
        ("~/Documents", Platform.LINUX, "not_absolute"),
        ("Users/alice", Platform.WINDOWS, "not_absolute"),
        ("/Users/alice", Platform.WINDOWS, "not_absolute"),
        # C:foo means "foo under the current directory of drive C".
        ("C:foo", Platform.WINDOWS, "drive_relative"),
        # An empty or control-character path is not a path.
        ("", Platform.LINUX, "empty"),
        ("   ", Platform.LINUX, "empty"),
        ("/home/alice/a" + chr(0) + "b", Platform.LINUX, "control_char"),
        # Climbing above the root is an error, not a clamp. Clamping is what
        # would turn this into a silent success on /etc.
        ("/granted/../../etc", Platform.LINUX, "escapes_root"),
        ("C:/granted/../../Windows", Platform.WINDOWS, "escapes_root"),
        # The Win32 device namespaces reach the filesystem through a parser that
        # does not resolve dots and does not strip them. Refuse rather than guess.
        ("//?/C:/Users/alice", Platform.WINDOWS, "device_prefix"),
        ("//./C:/Users/alice", Platform.WINDOWS, "device_prefix"),
        # A reserved device name is not a file under the directory.
        ("C:/granted/NUL.txt", Platform.WINDOWS, "reserved_name"),
        ("C:/granted/con", Platform.WINDOWS, "reserved_name"),
        ("C:/granted/COM1", Platform.WINDOWS, "reserved_name"),
        # A UNC path with no share is not a share root.
        ("//server", Platform.WINDOWS, "unc_incomplete"),
    ],
)
def test_refused_paths_are_refused_by_name(raw, platform, reason):
    with pytest.raises(PathRefused) as caught:
        normalize(raw, platform)
    assert caught.value.reason == reason


def test_unicode_dot_is_not_a_dot():
    """Only the ASCII dot and dot-dot are special.

    A fullwidth dot is an ordinary filename character, and treating it as a dot
    would let a segment be rewritten into something the filesystem never resolves
    that way — the exact class of disagreement this module exists to prevent.
    """
    assert normalize("/home/alice/\u3002\u3002/secret", Platform.LINUX).segments == (
        "home",
        "alice",
        "\u3002\u3002",
        "secret",
    )


@pytest.mark.parametrize(
    ("grant", "candidate", "expected"),
    [
        # The directory itself is inside itself.
        ("/home/alice/MyDocs", "/home/alice/MyDocs", True),
        ("/home/alice/MyDocs", "/home/alice/MyDocs/a.txt", True),
        ("/home/alice/MyDocs", "/home/alice/MyDocs/sub/deep/b.txt", True),
        # The bug this whole module exists for: a prefix is not containment.
        ("/home/alice/MyDocs", "/home/alice/MyDocuments/secret.txt", False),
        ("/home/alice/docs", "/home/alice/docs2", False),
        # A sibling, an ancestor, and somewhere else entirely.
        ("/home/alice/MyDocs", "/home/alice/Other", False),
        ("/home/alice/MyDocs", "/home/alice", False),
        ("/home/alice/MyDocs", "/etc/passwd", False),
        # A different root is a different filesystem.
        ("/home/alice", "//server/share/alice", False),
    ],
)
def test_containment_is_by_segment(grant, candidate, expected):
    assert (
        contains(
            normalize(grant, Platform.LINUX), normalize(candidate, Platform.LINUX)
        )
        is expected
    )


def test_case_folding_follows_the_filesystem_not_the_string():
    """Two spellings are one grant on Windows and macOS, two on Linux."""
    win_grant = normalize("C:/Users/Alice/MyDocs", Platform.WINDOWS)
    assert contains(win_grant, normalize("c:/users/alice/mydocs/x", Platform.WINDOWS))

    mac_grant = normalize("/Users/Alice/Docs", Platform.MACOS)
    assert contains(mac_grant, normalize("/Users/Alice/Docs/x", Platform.MACOS))
    assert contains(mac_grant, normalize("/users/alice/docs/x", Platform.MACOS))

    linux_grant = normalize("/home/Alice/docs", Platform.LINUX)
    assert not contains(linux_grant, normalize("/home/alice/docs/x", Platform.LINUX))


def test_escaping_and_normalizing_reach_the_same_answer():
    """A path that climbs out and comes back is the real directory, and is judged
    as that directory — the escape does not need to be refused to be safe, it just
    must not be believed."""
    grant = normalize("/home/alice/MyDocs", Platform.LINUX)
    sneaky = normalize("/home/alice/MyDocs/sub/../../Other/secret", Platform.LINUX)
    assert sneaky.text == "/home/alice/Other/secret"
    assert not contains(grant, sneaky)

    stays_inside = normalize("/home/alice/MyDocs/sub/../notes.txt", Platform.LINUX)
    assert stays_inside.text == "/home/alice/MyDocs/notes.txt"
    assert contains(grant, stays_inside)
