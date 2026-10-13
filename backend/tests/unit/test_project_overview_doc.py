"""项目目标：按小节写的老文档，注入每段对话的只有「项目是什么」那一块。

写进别的小节的正文，注入的那一份里一个字都不该出现——而一份没有这一节的文档
（写成项目目标的那种），不能因此整个消失。
"""

from app.domain.project.overview import project_brief

DOC = """## 项目是什么

给高中生做一套算法课的平台。做：课程、作业、评测；不做：直播。

## 现在在做什么

- 这一块不该有人手写。

## 大家都该知道的

- 老项目的旧小节。
"""


def test_only_the_project_brief_reaches_the_prompt():
    brief = project_brief(DOC)

    assert brief.startswith("给高中生做一套算法课的平台")
    # 手写进别的块的正文一概不注入：写在那里等于没写。
    assert "这一块不该有人手写" not in brief
    assert "老项目的旧小节" not in brief


def test_a_document_without_the_brief_heading_still_arrives():
    # 没有这一节的文档整篇注入，不能整个消失——静默丢掉项目共识更糟。
    assert project_brief("## 目标\n\n做一件事。\n") == "## 目标\n\n做一件事。"
