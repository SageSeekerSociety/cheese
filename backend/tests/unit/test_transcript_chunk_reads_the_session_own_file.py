"""An executor reads its session's transcript in pieces, and nothing else.

Claude Code writes a session's transcript on the session host. The machine a
session's tools run on does not have it, so a file tool or a shell there gets
"No such file" for the path after compaction. The fix gives that machine a
read-only view whose bytes come from here: the session's runner answers
`transcript_chunk`, looked up by its own session id under the config dir it was
launched with.

Two things this test holds:

* a piece comes back whole, at the offset asked for, and the last piece says it
  is the end;
* the caller names an offset and a size, never a path — so no spelling of a
  path can reach a file that is not this session's transcript;
* a size of 0 asks only *where* it is, which is the answer the executor mounts
  its view by, and which is still this session's own file.
"""

import base64

from app.domain.agent.harness.claude_code.runner import transcript_chunk

SESSION = "8b1d0f3a-5c2e-4e6a-9f11-2c3d4e5f6a7b"


def _config_dir(tmp_path):
    """A config dir holding one session's transcript under a project dir."""
    projects = tmp_path / "projects" / "-home-cheese--room"
    projects.mkdir(parents=True)
    (projects / f"{SESSION}.jsonl").write_bytes(b'{"a":1}\n{"b":2}\n')
    return tmp_path


def test_a_piece_comes_back_at_its_offset_and_the_end_is_marked(tmp_path):
    config = _config_dir(tmp_path)

    first = transcript_chunk(config, SESSION, offset=0, size=7)
    assert base64.b64decode(first["data"]) == b'{"a":1}'
    assert first["size"] == 16
    assert first["eof"] is False

    rest = transcript_chunk(config, SESSION, offset=7, size=100)
    assert base64.b64decode(rest["data"]) == b'\n{"b":2}\n'
    assert rest["eof"] is True


def test_a_transcript_that_is_not_there_yet_is_an_answer_not_an_error(tmp_path):
    config = _config_dir(tmp_path)

    assert transcript_chunk(config, "no-such-session", offset=0, size=100) == {
        "missing": True
    }


def test_the_file_is_found_by_the_caller_session_id_not_by_a_path(tmp_path):
    """A second session's transcript in the same tree is not reachable by it.

    The runner looks the file up by *its* session id, so a caller cannot name
    another session's transcript: asking for this session's id can only ever
    return this session's file.
    """
    config = _config_dir(tmp_path)
    other = config / "projects" / "-home-cheese--room" / "other.jsonl"
    other.write_bytes(b"someone else's conversation")

    piece = transcript_chunk(config, SESSION, offset=0, size=1000)
    assert b"someone else" not in base64.b64decode(piece["data"])


def test_reading_past_the_end_is_empty_and_still_the_end(tmp_path):
    config = _config_dir(tmp_path)

    piece = transcript_chunk(config, SESSION, offset=10_000, size=100)
    assert base64.b64decode(piece["data"]) == b""
    assert piece["eof"] is True


def test_a_zero_size_asks_where_it_is_without_pulling_any_of_it(tmp_path):
    """The answer names this host's spelling of the file, and no bytes.

    The executor mounts its read-only view at exactly the path the build puts
    in front of the model, and it learns that path from here — so the answer
    has to be the real one, and asking for it must not drag the file across.
    """
    config = _config_dir(tmp_path)

    answer = transcript_chunk(config, SESSION, offset=0, size=0)

    assert answer["path"] == str(
        config / "projects" / "-home-cheese--room" / f"{SESSION}.jsonl"
    )
    assert answer["size"] == 16
    assert answer["data"] == ""


def test_where_it_is_still_names_only_this_session(tmp_path):
    config = _config_dir(tmp_path)
    other = config / "projects" / "-home-cheese--room" / "other.jsonl"
    other.write_bytes(b"someone else's conversation")

    assert transcript_chunk(config, SESSION, offset=0, size=0)["path"] != str(other)
    assert transcript_chunk(config, "no-such-session", offset=0, size=0) == {
        "missing": True
    }
