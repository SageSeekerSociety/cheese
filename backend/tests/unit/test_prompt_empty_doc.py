"""文档还空着时，会话也要读到「建第一版」那一篇——不论坐进房间的是哪个模型。

只在有文档时说「维护它」，第一版就全靠模型自己悟：本项目近 30 天里 Kimi / MiMo
一篇都没建过。`""` 表示房间有文档位但空着，None 表示这一轮没有文档这回事（私聊、
巡检、总览房间自己那一轮），两者开场说的不一样。
"""

from app.domain.prompts import Facts, Kind, render_opening, selected


def test_a_room_with_a_document_place_reads_the_document_rules_empty_or_not():
    assert "system/doc_section" in selected(Kind.task, Facts(has_doc=True))


def test_an_empty_document_is_said_to_be_empty():
    empty = render_opening(Kind.task, doc="").sections["doc"]
    written = render_opening(Kind.task, doc="## 目标\n\n做一件事。\n").sections["doc"]

    assert "做一件事。" in written
    assert empty != written


def test_no_document_at_all_says_nothing_about_one():
    assert "system/doc_section" not in selected(Kind.task, Facts(has_doc=False))
    assert "doc" not in render_opening(Kind.task, doc=None).sections
