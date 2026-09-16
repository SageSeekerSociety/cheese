"""现场那一行读不读得懂。

每个用例都是真实流量里量出来的形状，不是想出来的：绝对路径吃满预览、命令以
``cd <长路径>;`` 开头、Bash 带着模型自己写的 description 却没人用。
"""

import uuid

from app.domain.agent.tool_preview import (
    DETAIL_MAX,
    PREVIEW_MAX,
    ToolPreview,
    command_preview,
    tool_detail,
    tool_preview,
    work_subpath,
)

PROJECT = uuid.UUID("de808b13-ffd2-4b8a-9d1d-fba7babe389f")
TOPIC = uuid.UUID("b40e51a6-d8f0-4492-8696-4104879bd940")
WORK = work_subpath(PROJECT, TOPIC)
ABS = f"/home/someone/{WORK}"


# ---- Bash: 模型自己写的说明就是现成的现场文案 ----


def test_bash_prefers_the_models_own_chinese_description():
    preview = tool_preview(
        "Bash",
        {"command": f"cd {ABS}; docker compose up -d", "description": "把数据库拉起来"},
        work_dir=WORK,
    )
    assert preview == ToolPreview("把数据库拉起来")


# ---- 英文说明不直接显示：先让解析器试一次 ----


def test_an_english_description_loses_to_a_chinese_template():
    # 「读取文件 · backend/app/main.py」既是中文又比一句英文更准。
    preview = tool_preview(
        "Bash",
        {"command": f"cd {ABS}; cat backend/app/main.py", "description": "Read main"},
        work_dir=WORK,
    )
    assert preview == ToolPreview("backend/app/main.py", "Read")


def test_an_english_description_still_beats_a_raw_command():
    # 解析器认不出来时，那句英文仍然胜过一行 shell —— 别为了「不要英文」把可读的
    # 句子换成不可读的命令。
    preview = tool_preview(
        "Bash",
        {
            "command": f"cd {ABS}; docker inspect -f '{{{{.State.Pid}}}}' api",
            "description": "Look up the container pid",
        },
        work_dir=WORK,
    )
    assert preview == ToolPreview("Look up the container pid")


def test_a_chinese_description_is_never_overridden_by_the_parser():
    # 模型已经用中文说清楚了要干什么，模板再准也不该顶掉它 —— 它知道「为什么」。
    preview = tool_preview(
        "Bash",
        {"command": "cat backend/app/main.py", "description": "看看启动时都装了什么"},
        work_dir=WORK,
    )
    assert preview == ToolPreview("看看启动时都装了什么")


def test_bash_falls_back_to_the_command_when_nothing_described_it():
    preview = tool_preview("Bash", {"command": "make release"}, work_dir=WORK)
    assert preview.text == "make release"


def test_bash_description_is_collapsed_and_capped():
    preview = tool_preview(
        "Bash", {"command": "x", "description": "查\n\t一下  " + "长" * 300}
    )
    assert "\n" not in preview.text
    assert len(preview.text) <= 120


def test_blank_description_is_not_a_description():
    preview = tool_preview("Bash", {"command": "make release", "description": "   "})
    assert preview.text == "make release"


# ---- 路径：读的人要看得出改的是哪个文件 ----


def test_file_path_is_trimmed_to_the_workspace():
    preview = tool_preview(
        "Read", {"file_path": f"{ABS}/frontend/src/lib/toolLabels.ts"}, work_dir=WORK
    )
    assert preview == ToolPreview("frontend/src/lib/toolLabels.ts")


def test_a_path_outside_the_workspace_keeps_its_tail():
    # /tmp 下那些又长又编码过的目录：整条显示等于什么都没显示。
    preview = tool_preview(
        "Write",
        {"file_path": "/tmp/claude-1000/-home-someone--cheese-work-de808b13/x/note.md"},
        work_dir=WORK,
    )
    assert preview.text == "…/x/note.md"


def test_a_short_path_is_left_alone():
    preview = tool_preview("Edit", {"file_path": "backend/app/main.py"}, work_dir=WORK)
    assert preview.text == "backend/app/main.py"


def test_the_workspace_root_itself_says_so():
    preview = tool_preview("Read", {"file_path": ABS}, work_dir=WORK)
    assert preview.text == "."


def test_trimming_still_works_without_a_workspace():
    # 老行、或者拿不到话题上下文的调用方：退到按段保留，不能整条糊上去。
    preview = tool_preview("Read", {"file_path": f"{ABS}/backend/app/main.py"})
    assert preview.text == "…/app/main.py"


# ---- 没有 description 的命令：剥掉没信息量的段落 ----


def test_leading_cd_is_dropped():
    preview = command_preview(f"cd {ABS}; make check", work_dir=WORK)
    assert preview.text == "make check"


def test_leading_export_and_assignment_are_dropped():
    preview = command_preview("export GH_TOKEN=x && SP=/tmp/a gh pr list")
    assert preview.text == "gh pr list"


def test_a_command_that_is_only_preparation_stays_as_is():
    # 说不出更好的说法时原样显示，别把它说成一件它不是的事。
    preview = command_preview(f"cd {ABS} && export A=1", work_dir=WORK)
    assert preview.action is None
    assert preview.text.startswith("cd ")


def test_separators_inside_quotes_do_not_split():
    preview = command_preview("""grep -n 'a; b && c' backend/app/main.py""")
    assert preview.action == "Grep"
    assert preview.text == "a; b && c"


def test_pipelines_stay_one_thing():
    preview = command_preview("git log --oneline | head -20")
    assert preview.text == "git log --oneline | head -20"


# ---- 认得出来的命令，动词也换成更贴切的那个 ----


def test_reading_a_file_reads_as_reading_a_file():
    preview = command_preview(f"cd {ABS}; cat backend/app/main.py", work_dir=WORK)
    assert preview == ToolPreview("backend/app/main.py", "Read")


def test_head_skips_the_value_of_its_count_flag():
    preview = command_preview("head -n 50 backend/app/main.py")
    assert preview == ToolPreview("backend/app/main.py", "Read")


def test_grep_shows_the_pattern_not_the_path():
    preview = command_preview("grep -rn --include=*.py TODO backend/")
    assert preview == ToolPreview("TODO", "Grep")


def test_find_prefers_the_name_it_is_looking_for():
    preview = command_preview("find . -maxdepth 3 -name '*.jsonl'")
    assert preview == ToolPreview("*.jsonl", "Glob")


def test_a_verb_with_no_operand_still_gets_the_better_verb():
    preview = command_preview("ls -la")
    assert preview == ToolPreview("ls -la", "Glob")


def test_an_unrecognised_command_is_never_guessed_at():
    preview = command_preview("docker inspect -f '{{.State.Pid}}' cheese-api-front")
    assert preview.action is None
    assert preview.text.startswith("docker inspect")


def test_a_full_path_command_is_recognised_by_its_last_segment():
    preview = command_preview("/usr/bin/cat backend/app/main.py")
    assert preview == ToolPreview("backend/app/main.py", "Read")


# ---- 其它工具 ----


def test_tools_without_a_telling_argument_show_only_their_verb():
    assert tool_preview("FutureTool", {"x": 1}) == ToolPreview()
    assert tool_preview("Read", {}) == ToolPreview()


def test_non_dict_input_is_survivable():
    assert tool_preview("Bash", "not a dict") == ToolPreview()  # type: ignore[arg-type]


def test_search_and_web_tools_keep_their_argument():
    assert tool_preview("Grep", {"pattern": "TODO"}).text == "TODO"
    assert tool_preview("WebFetch", {"url": "https://example.com"}).text == (
        "https://example.com"
    )


# ---- pi 原生工具：同样的动作，另一套名字和参数名 ----


def test_pi_shell_calls_are_parsed_like_any_other_shell_call():
    # pi 的 bash 没有 description 这个参数，所以只剩命令原文可读 —— 四档退让
    # 本来就是为这种情况写的。
    preview = tool_preview(
        "bash",
        {"command": f"cd {ABS}; cat backend/app/main.py"},
        work_dir=WORK,
    )
    assert preview == ToolPreview("backend/app/main.py", "Read")


def test_pi_file_tools_show_the_workspace_relative_path():
    assert tool_preview("read", {"path": f"{ABS}/hello.py"}, work_dir=WORK) == (
        ToolPreview("hello.py")
    )
    assert tool_preview(
        "write", {"path": f"{ABS}/notes.md", "content": "..."}, work_dir=WORK
    ) == ToolPreview("notes.md")
    assert tool_preview(
        "edit",
        {"path": f"{ABS}/hello.py", "edits": [{"oldText": "a", "newText": "b"}]},
        work_dir=WORK,
    ) == ToolPreview("hello.py")
    assert tool_preview("ls", {"path": f"{ABS}/backend"}, work_dir=WORK) == (
        ToolPreview("backend")
    )


def test_pi_search_tools_show_what_is_being_looked_for():
    assert tool_preview("grep", {"pattern": "TODO", "path": "backend"}).text == "TODO"
    assert tool_preview("find", {"pattern": "**/*.jsonl"}).text == "**/*.jsonl"


# ---- 重定向：这一段在写文件，不是在读 ----


def test_a_heredoc_written_through_cat_is_a_write():
    # agent 落脚本的常用写法。按 `cat` 的字面查表会显示成「读文件 · >」——
    # 一次写被说成读，参数还是个重定向符号。
    preview = command_preview("cat > /tmp/build_docx.py <<'EOF'")
    assert preview == ToolPreview("/tmp/build_docx.py", "Write")


def test_a_redirect_without_spaces_is_still_the_file_it_writes():
    assert command_preview("echo hi >>notes.md") == ToolPreview("notes.md", "Write")


def test_a_command_that_merely_redirects_its_output_is_not_a_write():
    # 做的是跑脚本，把它说成写 out.log 同样是说错。
    preview = command_preview("python3 build.py > out.log")
    assert preview.action is None
    assert preview.text == "python3 build.py > out.log"


def test_output_thrown_away_is_not_a_file_that_was_written():
    preview = command_preview("cat huge.log > /dev/null")
    assert preview.action is None


def test_merging_file_descriptors_is_not_a_redirect_target():
    preview = command_preview("cat notes.md 2>&1")
    assert preview == ToolPreview("notes.md", "Read")


# ---- 摊开这一行：参数原文 ----


def _detail(name: str, args: dict, *, work_dir: str = "") -> str:
    """按生产路径算：预览先出来，原文再拿它对照。"""
    return tool_detail(name, args, tool_preview(name, args, work_dir=work_dir))


def test_opening_a_rewritten_line_shows_the_command_that_was_run():
    # 一行显示的是「写文件 · build.py」——那是重写过的说法，heredoc 里的内容和
    # 重定向都不在上面。摊开的人要的正是这些。
    command = "cat > /tmp/build.py <<'EOF'\nprint(1)\nEOF"
    assert tool_preview("bash", {"command": command}).text == "/tmp/build.py"
    assert _detail("bash", {"command": command}) == command


def test_opening_a_long_command_is_not_cut_at_the_one_line_limit():
    command = "pytest " + " ".join(f"tests/test_{i}.py" for i in range(40))
    assert len(_detail("Bash", {"command": command})) > PREVIEW_MAX


def test_opening_a_shortened_path_shows_where_the_file_actually_is():
    args = {"path": f"{ABS}/backend/app/main.py"}
    assert tool_preview("read", args, work_dir=WORK).text == "backend/app/main.py"
    assert _detail("read", args, work_dir=WORK) == args["path"]


def test_a_chinese_description_still_opens_onto_the_command():
    args = {
        "command": f"cd {ABS}; docker compose up -d",
        "description": "把数据库拉起来",
    }
    assert _detail("Bash", args, work_dir=WORK) == args["command"]


def test_a_line_with_nothing_more_to_say_carries_no_second_copy():
    # 摊开之后看见同一句话，等于什么也没摊开。
    assert _detail("Grep", {"pattern": "TODO"}) == ""
    assert _detail("read", {"path": "notes.md"}) == ""
    assert _detail("Bash", {"command": "make test"}) == ""


def test_an_enormous_argument_is_capped_and_says_so():
    detail = _detail("Bash", {"command": "echo " + "x" * (DETAIL_MAX * 2)})
    assert len(detail) == DETAIL_MAX + 1
    assert detail.endswith("…")


def test_a_tool_with_no_telling_argument_has_nothing_to_open():
    assert _detail("FutureTool", {"x": 1}) == ""
    assert tool_detail("Bash", "not a dict", ToolPreview()) == ""  # type: ignore[arg-type]
