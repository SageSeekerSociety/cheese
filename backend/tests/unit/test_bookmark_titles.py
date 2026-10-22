"""A bookmark's rule name: the message's first meaningful line, cut to a name.

The model may later write a better one; this one is what every bookmark has
the moment it is made, so its rules are the reader's first impression.
"""

import pytest

from app.domain.chapter.naming import clean_name
from app.domain.chapter.titles import auto_title


@pytest.mark.parametrize(
    ("said", "title"),
    [
        # A mention addresses someone; it is not what the message is about.
        ("<@cheese> 先看登录", "先看登录"),
        # Markdown furniture is not part of the name.
        ("## 发布前回归", "发布前回归"),
        ("- 把三条路径再过一遍", "把三条路径再过一遍"),
        # A leading clause before a colon is already a name.
        ("登录报错：token 刷新之后仍然 401", "登录报错"),
        # A sentence ends the name; the rest is the message.
        ("你好，我在。\n有什么可以帮你的吗？", "你好，我在"),
        # Bold markers around the name go.
        ("**发布前回归**：把三条路径再过一遍", "发布前回归"),
        # Nothing said, nothing to name.
        ("", None),
        ("   \n\t", None),
    ],
)
def test_a_bookmark_takes_its_name_from_the_message(said, title):
    assert auto_title(said) == title


def test_a_long_name_is_cut_to_a_name():
    said = "把登录、刷新令牌、过期提示和所有边界情况都过一遍"

    title = auto_title(said, limit=12)

    assert title == said[:12] + "…"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("「空白会话问候」。", "空白会话问候"),
        ('标题："登录报错"', "登录报错"),
        ("   ", None),
    ],
)
def test_a_model_name_is_stripped_of_quotes_prefix_and_punctuation(raw, expected):
    assert clean_name(raw) == expected
