"""`declared.read` 的失败也要进缓存（减负 #944-adjacent）。

没读成过的项目（本地没 forge、或 forge 挂了）以前**每次读都重打一次必败的
forge 调用、重告一次警** —— 一轮两个调用者，每轮两条告警 + 两次白打。失败
结果与成功读同一条新鲜度规矩进缓存，60 秒后后台再试。
"""

import asyncio
import uuid

from app.domain.remote_mcp import declared


def test_a_failed_read_is_cached_not_retried_by_every_caller(monkeypatch) -> None:
    calls: list[int] = []

    async def boom(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("no forge")

    monkeypatch.setattr(declared, "_fetch", boom)
    declared._cache.clear()
    project_id = uuid.uuid4()

    def read():
        # `db` 在这条路上根本没人碰：唯一读者是被打掉的 `_fetch`。
        return asyncio.run(declared.read(object(), project_id))  # type: ignore[arg-type]

    first = read()
    second = read()

    assert first.problem == "unreadable" and second.problem == "unreadable"
    # 第二次没再打 forge：失败被缓存住了。
    assert len(calls) == 1
