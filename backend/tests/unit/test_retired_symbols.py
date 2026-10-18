"""退役符号的守卫：这五个名字已经清退，不许再回到代码里。

清退的依据是零引用，不是「看着没人用」：

- `app.domain.repository.service` 的 `SANDBOX_SESSIONS_ROOT`、`sessions_root`、
  `sandbox_session_dir`：一个常量和两个路径助手。全仓在用的同族名字是
  `SANDBOX_TOPICS_ROOT`、`sandbox_topic_workdir` 和 `session_dir`，它们必须留着。
- `app.domain.knowledge.models.KnowledgeSource`：同文件的 `KnowledgeType` 仍在使用。
- `app.domain.agent.machine_tunnel._OP_CONT`：同一组 opcode 常量里只有它没有读者，
  `_OP_BIN`、`_OP_CLOSE`、`_OP_PING`、`_OP_PONG` 都还在用。

名字活过来，说明有人重新造了一份；清退时留在原处的邻居被顺手删掉，同样红。两边
一起钉，下一次整理才不会把「没人用的」和「还在用的」看成一类。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest

#: (模块, 已清退的名字)。名字回到模块上就红。
RETIRED: dict[str, list[str]] = {
    "app.domain.repository.service": [
        "SANDBOX_SESSIONS_ROOT",
        "sessions_root",
        "sandbox_session_dir",
    ],
    "app.domain.knowledge.models": ["KnowledgeSource"],
    "app.domain.agent.machine_tunnel": ["_OP_CONT"],
}

#: (模块, 必须留下的相邻名字)。清退时把这些一起删掉就红。
KEPT: dict[str, list[str]] = {
    "app.domain.repository.service": [
        "SANDBOX_TOPICS_ROOT",
        "sandbox_topic_workdir",
        "session_dir",
    ],
    "app.domain.knowledge.models": ["KnowledgeType"],
    "app.domain.agent.machine_tunnel": [
        "_OP_BIN",
        "_OP_CLOSE",
        "_OP_PING",
        "_OP_PONG",
    ],
}


def _cases(table: dict[str, list[str]]) -> Iterator[Any]:
    for module_name, names in table.items():
        for name in names:
            yield pytest.param(module_name, name, id=f"{module_name}:{name}")


@pytest.mark.parametrize(("module_name", "name"), list(_cases(RETIRED)))
def test_a_retired_name_is_gone(module_name: str, name: str) -> None:
    module = importlib.import_module(module_name)
    assert not hasattr(module, name), (
        f"{module_name}.{name} 已按零引用清退。要在原处复活它，先说明谁调用、"
        "为什么现有的同族名字做不了这件事。"
    )


@pytest.mark.parametrize(("module_name", "name"), list(_cases(KEPT)))
def test_the_name_that_stayed_is_still_there(module_name: str, name: str) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, name), (
        f"{module_name}.{name} 还在被使用，清退时不能连它一起删。"
    )
