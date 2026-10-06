"""A task's video link survives the move into its description, in front of the
text the publisher wrote, whichever of the two description formats it has."""

import importlib.util
import json
from pathlib import Path


def migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/694b0dbaf5eb_task_video_moves_into_its_description.py"
    )
    spec = importlib.util.spec_from_file_location("task_video_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BILIBILI = "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
WRITTEN = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "复现基线"}]}
    ],
}


def test_a_bilibili_video_becomes_the_player_before_the_written_text():
    moved = json.loads(migration().with_video(json.dumps(WRITTEN), f"  {BILIBILI} "))
    assert moved["content"][0] == {"type": "video", "attrs": {"src": BILIBILI}}
    assert moved["content"][1:] == WRITTEN["content"]


def test_a_link_to_another_site_stays_a_link_the_reader_can_open():
    url = "https://example.edu/lecture/3"
    moved = json.loads(migration().with_video(json.dumps(WRITTEN), url))
    first = moved["content"][0]
    assert first["type"] == "paragraph"
    assert first["content"][0]["text"] == url
    assert first["content"][0]["marks"][0]["attrs"]["href"] == url
    assert moved["content"][1:] == WRITTEN["content"]


def test_a_markdown_description_gets_the_link_as_its_own_first_line():
    moved = migration().with_video("## 要求\n\n- 提交代码", BILIBILI)
    assert moved == f"{BILIBILI}\n\n## 要求\n\n- 提交代码"


def test_an_empty_description_becomes_just_the_link():
    assert migration().with_video("", BILIBILI) == BILIBILI
    assert migration().with_video(None, BILIBILI) == BILIBILI
