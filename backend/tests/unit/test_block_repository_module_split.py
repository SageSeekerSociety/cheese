"""`block/repositories.py` 拆成四个模块之后，它那一面必须和拆之前一样。

拆的是文件，不是行为。三件事要守住：

**旧路径照旧回答。** 仓库内有五十多个调用点按 `from app.domain.block.repositories
import BlockRepository` 取这个类，`BlockPage` 也被这个路径取过。搬走的方法留在类上
（`BlockRepository` 继承那四个模块的基类），`BlockPage` 由 `repositories.py` 再导出。

**再导出不是再定义。** 两边必须是同一个对象。拆开之后最可能的退化是有人照着又写一份
`BlockPage` —— 两条路径给出两个不同的类，`isinstance` 就会在两个都用上的地方静默为
假，而 `dataclass(frozen=True)` 的相等语义看起来一切正常。

**按名字打的补丁仍然打得到。** 有两处按**类对象**取的属性：
`tests/integration/test_block_paging.py` 把 `BlockRepository.reactions_for_blocks`
换成探针，`scripts/perf-awaiting-me/measure_awaiting_me.py` 把
`BlockRepository.awaiting_answer_blocks` 换成计时壳。它们先拿类再取属性，只要方法还在
`BlockRepository` 上（自己定义的或继承来的都算），补丁就照旧生效；这里钉的是「取得到、
且取到的是实现它的那个函数」。
"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block import awaiting, history, paging, reactions, repositories
from app.domain.block.repositories import BlockPage, BlockRepository

#: 拆分前住在 `repositories.py` 的方法，现在由哪个基类定义。
MOVED = {
    awaiting.AwaitingQueries: (
        "awaiting_an_answer",
        "awaiting_answer_blocks",
        "questions_a_reply_answers",
    ),
    paging.PagingQueries: (
        "list_for_topic",
        "page_for_topic",
        "count_for_topic",
        "holds",
        "count_messages",
    ),
    history.HistoryQueries: (
        "turn_history",
        "latest_for_topic",
        "messages_before",
        "last_messages",
        "between",
    ),
    reactions.Reactions: (
        "toggle_reaction",
        "add_reaction_if_absent",
        "reactions_for_blocks",
        "reactions_for_block",
    ),
}


def test_block_page_is_reexported_not_redefined() -> None:
    assert repositories.BlockPage is paging.BlockPage, (
        "`app.domain.block.repositories.BlockPage` 和 `paging.BlockPage` 是两个对象 —— "
        "这是把类又定义了一遍，不是再导出。"
    )
    assert BlockPage is paging.BlockPage


def test_every_moved_method_still_answers_on_the_repository_class() -> None:
    missing = [
        f"{name}（应在 {base.__name__}）"
        for base, names in MOVED.items()
        for name in names
        if not hasattr(BlockRepository, name)
    ]
    assert not missing, f"这些方法不在 `BlockRepository` 上了：{missing}"


def test_a_moved_method_is_the_function_that_defines_it() -> None:
    """取到的不只是「同名可调用」，而是搬过去的那一份实现 ——
    在 `BlockRepository` 里重写一份同名方法，取属性照样成功，只是补丁与调用会走上
    另一份代码。"""
    wrong = [
        f"{name}: 类上是 {getattr(BlockRepository, name)}, "
        f"{base.__module__}.{base.__qualname__} 里是 {base.__dict__[name]}"
        for base, names in MOVED.items()
        for name in names
        if name not in base.__dict__
        or getattr(BlockRepository, name) is not base.__dict__[name]
    ]
    assert not wrong, "这些方法不是由它现在所在的基类提供的：\n" + "\n".join(wrong)


def test_a_patch_on_the_repository_class_still_reaches_the_method(monkeypatch) -> None:
    """两处 monkeypatch 都打在类对象上：`BlockRepository.reactions_for_blocks`
    与 `BlockRepository.awaiting_answer_blocks`。打在类上的属性查找先看类自己、再看
    基类，方法从继承来也照样被换掉 —— 形参名得和实现一致，否则调用当场
    `TypeError`，这正是补丁生效与否最直接的证据。"""
    repo = BlockRepository(cast(AsyncSession, None))

    async def a_probe(self, block_ids):  # noqa: ANN001, ANN202
        return {"probe": True}

    monkeypatch.setattr(BlockRepository, "reactions_for_blocks", a_probe)
    monkeypatch.setattr(BlockRepository, "awaiting_answer_blocks", a_probe)
    # 经实例取到的是绑定方法，比的是它下面那个函数 —— 也就是补上去的那一个。
    assert repo.reactions_for_blocks.__func__ is a_probe
    assert repo.awaiting_answer_blocks.__func__ is a_probe


def test_the_awaiting_statement_cache_is_one_object_through_the_class() -> None:
    """`_awaiting_statement` 是 classmethod，`cls._awaiting_statements` 走 MRO 拿到的
    必须是同一个 dict —— 否则每个基类各建一份，建一次处处复用就没了，
    而量出来的那 406µs / 449µs 省在哪里也说不清。"""
    assert (
        BlockRepository._awaiting_statements
        is awaiting.AwaitingQueries._awaiting_statements
    )
