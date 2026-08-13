"""Transcript-fork clone (fusion-design §6): pure logic + a real-file end-to-end.

No container / docker: the tmux backend clones by host-side file ops (both topics'
~/.claude are host-mounted), so the whole copy+fork path is exercised with real
files in tmp.
"""

import json

import pytest

from app.domain.agent import clone


def test_slug_for_matches_claude_project_dir():
    # Claude replaces every non-alphanumeric cwd character with '-'.
    assert clone.slug_for("/work") == "-work"
    assert clone.slug_for("/home/u/repo/App") == "-home-u-repo-App"
    assert clone.slug_for("/a.b/c") == "-a-b-c"
    # Underscores too (topic workdirs are /topics/topic_<hex> — verified
    # against live ~/.claude/projects entries).
    assert clone.slug_for("/topics/topic_ab12cd34") == "-topics-topic-ab12cd34"


def test_fork_transcript_rewrites_every_session_id():
    old, new = "aaaa-old-id", "bbbb-new-id"
    data = f'{{"sessionId":"{old}","x":1}}\n{{"sessionId":"{old}","x":2}}\n'.encode()
    forked = clone.fork_transcript(data, old, new)
    assert old.encode() not in forked
    assert forked.count(new.encode()) == 2


def test_fork_transcript_leaves_other_content_untouched():
    old, new = "sid-1", "sid-2"
    # Credentials / unrelated content must survive verbatim — only the id changes.
    data = b'{"sessionId":"sid-1","token":"secret-xyz","note":"keep me"}'
    forked = clone.fork_transcript(data, old, new)
    assert b"secret-xyz" in forked
    assert b"keep me" in forked
    assert b'"sessionId":"sid-2"' in forked


def test_transcript_file_path_layout(tmp_path):
    p = clone.transcript_file(tmp_path, "abc123", cwd="/topics/topic_ab12")
    # <session_dir>/projects/<slug(cwd)>/<sid>.jsonl
    assert p == tmp_path / "projects" / "-topics-topic-ab12" / "abc123.jsonl"


def test_find_transcript_under_any_slug(tmp_path):
    # A transcript written under the legacy /work slug is still found (reads
    # key off the unique session id, not the slug).
    f = clone.transcript_file(tmp_path, "sid-1", cwd=clone.LEGACY_CONTAINER_CWD)
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    assert clone.find_transcript(tmp_path, "sid-1") == f
    assert clone.find_transcript(tmp_path, "sid-2") is None


def test_mint_session_id_is_unique():
    assert clone.mint_session_id() != clone.mint_session_id()


def test_clone_transcript_files_end_to_end(tmp_path):
    """Real files: source transcript → forked copy under the target's slug.

    The source sits under the LEGACY /work slug (as every pre-project-mount
    transcript does) and must still be found; the fork lands under the slug of
    the target topic's own workdir, where Claude's --resume will look."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    old_sid, new_sid = "src-session-uuid", "dst-session-uuid"
    target_cwd = "/topics/topic_dd34ee56"

    src_file = clone.transcript_file(src_dir, old_sid, cwd=clone.LEGACY_CONTAINER_CWD)
    src_file.parent.mkdir(parents=True)
    lines = [
        {"sessionId": old_sid, "type": "user", "text": "hi"},
        {"sessionId": old_sid, "type": "assistant", "text": "hello"},
    ]
    src_file.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    clone.clone_transcript_files(
        source_session_dir=src_dir,
        source_session_id=old_sid,
        target_session_dir=dst_dir,
        new_session_id=new_sid,
        target_cwd=target_cwd,
    )

    dst_file = clone.transcript_file(dst_dir, new_sid, cwd=target_cwd)
    assert dst_file.is_file()
    body = dst_file.read_text(encoding="utf-8")
    # Every record carries the NEW id; the old id is gone (a clean fork).
    assert old_sid not in body
    assert body.count(new_sid) == 2
    # Content (the actual conversation) is preserved.
    assert '"text": "hello"' in body or '"hello"' in body
    # World-writable so the container's node user can keep appending.
    assert (dst_file.stat().st_mode & 0o666) == 0o666


def test_clone_transcript_files_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        clone.clone_transcript_files(
            source_session_dir=tmp_path / "nope",
            source_session_id="x",
            target_session_dir=tmp_path / "dst",
            new_session_id="y",
            target_cwd="/topics/topic_ab12",
        )


def test_clone_transcript_files_empty_source_raises(tmp_path):
    src_dir = tmp_path / "src"
    f = clone.transcript_file(src_dir, "empty", cwd=clone.LEGACY_CONTAINER_CWD)
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        clone.clone_transcript_files(
            source_session_dir=src_dir,
            source_session_id="empty",
            target_session_dir=tmp_path / "dst",
            new_session_id="y",
            target_cwd="/topics/topic_ab12",
        )
