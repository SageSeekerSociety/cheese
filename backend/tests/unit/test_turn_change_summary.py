"""一轮的改动汇总: 「这一轮改了 N 个文件（+X -Y）」 as one bounded event.

The room already showed every 写文件 / 改文件 / 执行命令 as it happened; what it
had no line for was the turn's net result. These tests cover the two pure halves
— reading per-file counts out of a diff, and turning them into the one line the
room shows — because the git half is environment and this half is the contract.
"""

from app.domain.agent.chat import (
    _CHANGE_FILES_LISTED,
    _change_summary_meta,
    _Changeset,
    _diff_file_stats,
    _format_change_summary,
)

DIFF = """diff --git a/backend/app/x.py b/backend/app/x.py
index 111..222 100644
--- a/backend/app/x.py
+++ b/backend/app/x.py
@@ -1,3 +1,4 @@
 keep
-gone
+added one
+added two
diff --git a/README.md b/README.md
index 333..444 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old title
+new title
"""


def test_stats_are_per_file_and_skip_the_headers():
    assert _diff_file_stats(DIFF) == [
        {"path": "backend/app/x.py", "added": 2, "removed": 1},
        {"path": "README.md", "added": 1, "removed": 1},
    ]


def test_a_commit_message_body_is_not_counted_as_changes():
    """`git show` prints the message before the diff; its lines are indented, so
    nothing before the first `diff --git` may be attributed to a file."""
    shown = (
        "commit deadbeef\n"
        "Author: 芝士 <cheese@zhishi.local>\n"
        "\n"
        "    chore: snapshot\n"
        "\n"
        "    -not a deletion\n"
        "    +not an addition\n"
        "\n" + DIFF
    )
    assert _diff_file_stats(shown) == _diff_file_stats(DIFF)


def test_a_rename_is_filed_under_its_new_path():
    diff = (
        "diff --git a/old/name.py b/new/name.py\n"
        "similarity index 90%\n"
        "rename from old/name.py\n"
        "rename to new/name.py\n"
        "--- a/old/name.py\n"
        "+++ b/new/name.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    assert _diff_file_stats(diff) == [{"path": "new/name.py", "added": 1, "removed": 1}]


def test_an_empty_diff_has_no_files():
    assert _diff_file_stats("") == []


def test_the_room_line_leads_with_the_counts():
    line = _format_change_summary(_diff_file_stats(DIFF))
    head, paths = line.split("\n")
    assert head == "这一轮改了 2 个文件（+3 -2）"
    assert paths == "backend/app/x.py · README.md"


def test_the_path_list_is_bounded_and_says_how_many_it_dropped():
    """不刷屏: a 60-file turn must not paste 60 paths into the timeline."""
    files = [{"path": f"f{i}.py", "added": 1, "removed": 0} for i in range(60)]
    head, paths = _format_change_summary(files).split("\n")
    assert head == "这一轮改了 60 个文件（+60 -0）"
    listed = paths.split(" · ")
    assert len(listed) == _CHANGE_FILES_LISTED + 1
    assert listed[-1] == f"…另 {60 - _CHANGE_FILES_LISTED} 个"


def test_meta_carries_the_commit_a_ui_would_open():
    """「点开看 diff」 needs a ref: GET /api/projects/{p}/git/diff?ref=<commit>
    already serves exactly this commit's diff."""
    files = _diff_file_stats(DIFF)
    meta = _change_summary_meta(_Changeset(["abc1234", "def5678"], files))
    assert meta["platform"] is True
    changeset = meta["changeset"]
    assert changeset["commit"] == "abc1234"  # newest first
    assert changeset["commits"] == ["abc1234", "def5678"]
    assert changeset["files_total"] == 2
    assert changeset["added"] == 3
    assert changeset["removed"] == 2
    assert changeset["files"] == files
    assert changeset["files_omitted"] == 0


def test_meta_file_list_is_bounded_too():
    files = [{"path": f"f{i}.py", "added": 1, "removed": 0} for i in range(60)]
    changeset = _change_summary_meta(_Changeset(["abc1234"], files))["changeset"]
    assert len(changeset["files"]) == _CHANGE_FILES_LISTED
    assert changeset["files_omitted"] == 60 - _CHANGE_FILES_LISTED
    assert changeset["files_total"] == 60  # the count is still honest


def test_no_meta_tool_key_so_todays_frontend_renders_the_text():
    meta = _change_summary_meta(_Changeset(["abc1234"], _diff_file_stats(DIFF)))
    assert "tool" not in meta
