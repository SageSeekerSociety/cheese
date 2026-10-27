"""A step of a person's 芝士 that read something says what it read by name, in
place of the reference the call was given: a space, a team, a 题目, a help page,
as well as a channel, a task, a project or a document."""

import pytest

from app.domain.agent.harness.pi.runner import named


def _came_back(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("频道「综合」（ml/channels/1），项目「机器学习」", "频道「综合」"),
        (
            "空间「操作系统 2026 秋」（spaces/3）\n成员 40 人",
            "空间「操作系统 2026 秋」",
        ),
        ("团队「课题组」（teams/7），5 人：\n- alice", "团队「课题组」"),
        ("# 01 背包动态规划\n\n第 2 版\n", "01 背包动态规划"),
        (
            "> ## Documentation Index\n> Fetch the index at: https://docs.example/llms.txt\n\n"
            "# 验收与采纳\n\n采纳之后……",
            "验收与采纳",
        ),
    ],
)
def test_what_was_read_is_named(text, name):
    assert named(_came_back(text)) == name


def test_what_names_nothing_it_read_is_not_named():
    assert named(_came_back("已在「综合」发出。")) is None
    assert named(_came_back("你加入的空间（2 个）：\n- 空间「A」")) is None
