"""What a person's 芝士 did while answering is kept with the answer, read off
the question's stream: how long it reasoned, each call with its arguments and
whether it failed, each draft write and each publish."""

from app.domain.agent.personal.service import parts_of


def test_the_stream_is_kept_as_steps_edits_and_published():
    told = [
        ("1000-0", "thinking", {}),
        ("3500-0", "tool", {"name": "cheese_problem_write", "call": "c1"}),
        ("3600-0", "tool_args", {"call": "c1", "text": '{"name":"La', "at": 0}),
        ("3700-0", "tool_args", {"call": "c1", "text": 'b 3"}', "at": 11}),
        (
            "3900-0",
            "change",
            {"draft": 41, "changed": {"name": {"before": "", "after": "Lab 3"}}},
        ),
        ("4000-0", "tool", {"name": "cheese_problem_publish", "call": "c2"}),
        ("4100-0", "tool_failed", {"call": "c2", "text": "草稿还不完整"}),
        ("4200-0", "delta", {"text": "还缺描述。", "at": 0}),
    ]

    parts = parts_of(told)

    assert parts == {
        "steps": [
            {"call": "thinking-0", "name": "thinking", "args": "", "ms": 2500},
            {"call": "c1", "name": "cheese_problem_write", "args": '{"name":"Lab 3"}'},
            {
                "call": "c2",
                "name": "cheese_problem_publish",
                "args": "",
                "failed": "草稿还不完整",
            },
        ],
        "edits": [{"draft": 41, "changed": {"name": {"before": "", "after": "Lab 3"}}}],
        "published": [],
    }


def test_an_answer_that_only_spoke_keeps_no_parts():
    assert parts_of([("1-0", "delta", {"text": "好。", "at": 0})]) is None


def test_a_file_handed_over_is_kept_with_the_answer():
    file = {"team": 9, "path": "成绩分布.png", "bytes": 84000}
    told = [
        ("1000-0", "tool", {"name": "cheese_file_give", "call": "g1"}),
        ("1500-0", "handed", file),
    ]

    parts = parts_of(told)

    assert parts is not None
    assert parts["handed"] == [file]
