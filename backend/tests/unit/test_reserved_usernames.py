"""平台用来表示「不是人」的那几个字符串，人不能注册（#345）。

`anonymous` 是「没有凭据的调用方」的哨兵值（`app/api/auth.py`），`system` 是平台
自己发通知时的作者，`cheese` / `cheese-<topic>` 是芝士和她的每话题分身。这些字符
串同时是合法用户名——用户名的唯一校验是 `^[a-zA-Z0-9_-]+$`，没有保留字表。

真有账号叫上其中一个，两件事会同时坏掉：每一条「未识别调用方」的日志、每一条
`author='anonymous'` 的历史消息都会读起来像那个人干的；而按字符串特判的授权代码
会在替一个真实用户做决定。#344 那条逃生口就是因为这个不敢把「owner 是 anonymous」
算成「没人管」。

这里钉的是**窄的那一半**：不再产生新的碰撞。宽的那一半（把哨兵换成 None）没做，
所以下面没有任何一条断言现有的哨兵读法。

**最要紧的是最后一组**：挡错了会把芝士自己挡在门外，平台连启动都启动不了。
"""

import pytest

from app.domain.identity.handles import is_reserved_username, topic_agent_handle


@pytest.mark.parametrize(
    "name",
    [
        "anonymous",
        "system",
        "cheese",
        "cheese-0123456789ab",  # 每话题分身的形状
    ],
)
def test_the_platforms_own_words_are_not_available_to_people(name: str):
    assert is_reserved_username(name) is True


@pytest.mark.parametrize("name", ["Anonymous", "ANONYMOUS", "System", "CHEESE"])
def test_reserving_is_case_folded(name: str):
    """`Anonymous` 在代码里不会和哨兵撞，但在「谁说的这句话」这件事上照撞不误。"""
    assert is_reserved_username(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "anonymous_zhang",  # 只是以它开头，不是它
        "cheeseburger",  # `cheese` 的前缀但不是分身命名空间（要 `cheese-`）
        "systemd",
        "wangchangxin",
        "andylizf",
        "a",
    ],
)
def test_ordinary_names_that_merely_look_similar_stay_available(name: str):
    """保留字表要窄。把 `cheeseburger` 一起挡掉是拿真实用户的名字去换心安。"""
    assert is_reserved_username(name) is False


def test_a_real_topic_agent_handle_is_reserved():
    """不写死形状：直接问生成器要一个真的分身 handle。"""
    import uuid

    assert is_reserved_username(topic_agent_handle(uuid.uuid4())) is True


# 「这个检查会不会把芝士自己挡在门外」是这个改动最危险的地方，但那要真的建一行
# 用户才能回答，所以它是一条 DB 测试：见
# tests/integration/test_reserved_usernames_api.py。
