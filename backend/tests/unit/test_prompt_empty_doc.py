"""文档还空着时，提示词也要说「建第一版」——不论坐进房间的是哪个模型。

只在有文档时说「维护它」，第一版就全靠模型自己悟：本项目近 30 天里 Kimi / MiMo
一篇都没建过。`""` 表示房间有文档位但空着，None 表示这一轮没有文档这回事（私聊、
巡检、总览房间自己那一轮），两者必须说不一样的话。
"""

from app.domain.agent.harness.prompt import build_system_prompt


def _prompt(doc: str | None) -> str:
    return build_system_prompt("base", "", doc, [])


def test_an_empty_room_doc_asks_for_the_first_version():
    prompt = _prompt("")

    assert "## 当前话题的实况文档（还没有）" in prompt
    assert "`cheese_doc_set` 建第一版" in prompt
    assert "不论你是哪个队友" in prompt
    # 五块模板和有文档时是同一份。
    assert "- **现状**" in prompt


def test_an_existing_doc_keeps_the_maintain_section_and_the_same_form():
    prompt = _prompt("## 目标\n\n做一件事。\n")

    # 改已有文档用 doc_edit：doc_set 整份覆盖，会盖掉别人的段落和正在打的字。
    assert "`cheese_doc_edit`" in prompt
    assert "（还没有）" not in prompt
    assert "- **现状**" in prompt
    assert "做一件事。" in prompt


def test_no_doc_at_all_says_nothing_about_the_room_doc():
    prompt = _prompt(None)

    assert "当前话题的实况文档" not in prompt
    assert "建第一版" not in prompt
