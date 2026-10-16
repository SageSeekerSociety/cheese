"""`ChatService` 拆成四块之后，边界自己站得住：

- 四块职责各有自己的模块，`chat.py` 里没有第二份定义（门面是同族继承，不是复制）；
- 搬走的方法仍然是同一个函数对象——调用点、子类重载（`tests/support/work_chat.py`
  的 `RacingSeat`）和测试打在类/实例上的 monkeypatch 用的都是旧名字；
- 新模块不反向 import `app.domain.agent.chat`，否则门面就成了循环。

行为本身由既有测试从门面那条路径覆盖；这里补的是拆分之后新出现的两样东西：模块
边界，和「`chat.py` 只剩下组合根」。
"""

import ast
import pathlib

import pytest

from app.domain.agent import (
    chat,
    chat_admission,
    chat_lifecycle,
    chat_live_input,
    chat_places,
)

#: (这一块的 mixin 类, 它的模块, 这一块的方法名)。方法名一个都不能少：漏掉一个，
#: 那个方法就可能悄悄在 chat.py 里留下第二份定义而这里还是绿的。
BLOCKS = [
    (
        chat_admission.TurnAdmission,
        chat_admission,
        ("_turn_seat_handle", "_prompt_lock", "converse", "converse_prepared"),
    ),
    (
        chat_live_input.LiveInput,
        chat_live_input,
        (
            "merge_into_running_turn",
            "notify_running_turn",
            "oldest_unread_at",
            "_input_registrar",
            "has_unread_input",
            "pending_seat",
            "session_took_over",
        ),
    ),
    (
        chat_places.ConversationPlaces,
        chat_places,
        (
            "post_system_event",
            "_save_session_pointer",
            "_room_of_conversation",
            "_conversation_place",
            "thread_replied",
            "forget_conversation",
        ),
    ),
    (
        chat_lifecycle.TurnLifecycle,
        chat_lifecycle,
        (
            "dismiss",
            "stop_work",
            "stop_work_in_closed_tasks",
            "_close_turn_interval",
            "session_controls",
            "recover_native_tools",
            "has_running_turn",
            "_confirm_turn_row_open",
            "_confirm_turn_row_closed",
            "edit_environment",
            "work_policy",
            "_close_open_turns",
            "row_is_dead",
            "seat_state",
            "has_live_screen",
            "turns_that_produced_something",
            "stop_listening",
            "_set_hook_activity",
            "_note_reachability",
        ),
    ),
]


def _defined_on_the_root() -> set[str]:
    """`chat.py` 的 `ChatService` 类体里直接定义的方法名。"""
    tree = ast.parse(pathlib.Path(chat.__file__).read_text())
    root = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChatService"
    )
    return {
        node.name
        for node in root.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _imported_modules(module) -> set[str]:
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    modules |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    return modules


def test_the_moved_methods_are_the_same_objects_behind_the_root():
    """门面是同族继承：同一个函数对象，不是一行行转发。"""
    for cls, module, names in BLOCKS:
        for name in names:
            method = getattr(chat.ChatService, name)
            assert method is getattr(cls, name), name
            assert method.__module__ == module.__name__, name


def test_the_root_carries_every_block_in_its_mro():
    assert set(chat.ChatService.__mro__) >= {cls for cls, _, _ in BLOCKS}
    # 拆分之前那一块还在，顺序没被换掉。
    assert chat.ChatService.__mro__.index(chat.SessionRecovery) > max(
        chat.ChatService.__mro__.index(cls) for cls, _, _ in BLOCKS
    )


def test_the_definitions_are_not_left_behind_in_the_root():
    """两份定义会各自漂移：搬走了就不能在 chat.py 里再写一遍。"""
    moved = {name for _, _, names in BLOCKS for name in names}
    left = _defined_on_the_root() & moved
    assert not left, sorted(left)


@pytest.mark.parametrize(
    "module",
    [chat_admission, chat_live_input, chat_places, chat_lifecycle],
    ids=["admission", "live_input", "places", "lifecycle"],
)
def test_a_block_does_not_import_the_facade_back(module):
    """反向 import 就是循环，门面也就不是门面了。"""
    assert "app.domain.agent.chat" not in _imported_modules(module)
