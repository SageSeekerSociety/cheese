"""聊天规则的两半：常驻的是时机，细则是按需（#1535 小洞清理之一）。

chat 全文原来每轮全量重发（~4.6K 字符），其中「怎么说话/怎么发布/和文档怎么配合」
是用到才需要的细则。拆成 chat + chat-detail 后，常驻指针必须说实话——声称「聊天说
明已在下方」而下面只有半份，是最糟的一种：模型以为已经拿到了全部规则。
"""

from app.domain.agent.skills import (
    DEFAULT_CHAT_SKILLS,
    NATIVE_CHAT_GUIDANCE,
    load_skills,
    native_skill_files,
)


def test_the_resident_guide_no_longer_claims_to_be_the_whole_guide():
    assert "已在下方提供，无需调用" not in NATIVE_CHAT_GUIDANCE
    # 不叫 cheese-chat：device_launch 每次复用都会 rm -f 那个退役旧名。
    assert "chat-detail" in NATIVE_CHAT_GUIDANCE


def test_the_two_halves_still_reconstruct_the_whole_guide():
    whole = load_skills(["chat", "chat-detail"])

    for section in (
        "# Chat as a collaborator's timeline",
        "## When to speak",
        "## How to sound",
        "## Publishing",
        "## Chat and documents",
    ):
        assert section in whole


def test_api_callers_without_a_skill_loader_get_both_halves_inline():
    assert {"chat", "chat-detail"} <= set(DEFAULT_CHAT_SKILLS)
    inline = load_skills(DEFAULT_CHAT_SKILLS)
    assert "## How to sound" in inline


def test_the_lazy_half_travels_to_the_session_as_a_native_skill():
    shipped = native_skill_files()
    assert "skills/chat-detail/SKILL.md" in shipped
    assert "## Publishing" in shipped["skills/chat-detail/SKILL.md"]
