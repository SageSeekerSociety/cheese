"""摘要邮件的正文：一条一行、每行指向它说的那件事（设计稿「摘要频率」）。"""

from app.domain.notification.digest import compose_digest


def test_the_subject_counts_the_items():
    subject, _body, _text = compose_digest(
        [{"type": "MENTION", "payload": {}}, {"type": "REPLY", "payload": {}}]
    )
    assert "2" in subject


def test_each_line_links_to_its_own_subject():
    _subject, body, _text = compose_digest(
        [
            {
                "type": "MENTION",
                "payload": {
                    "projectId": "22222222-2222-2222-2222-222222222222",
                    "topicId": "11111111-1111-1111-1111-111111111111",
                },
            }
        ]
    )
    # 深链里有那一条的 topic，而不是站点根。
    assert "11111111-1111-1111-1111-111111111111" in body


def test_each_line_says_what_happened_in_words():
    _subject, body, _text = compose_digest([{"type": "MENTION", "payload": {}}])
    assert "提到了你" in body
