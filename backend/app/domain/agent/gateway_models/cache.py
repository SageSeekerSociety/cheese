"""网关侧答案的 15 秒缓存：模型表、密钥表、用量窗口共用这一份。

缓存的是**网关的答案**（`AdminModel` 列表、`AdminKey` 列表、`UsageWindow`），不是拼好
的页面 dict —— 平台那半每次现查，所以改了算力额度不必等缓存过期。

`_cache` 与 `_cache_generation` 是模块级状态，`reset_cache()` 是清它的唯一出口：每个写
操作成功后由 `answers._after_write` 调一次，单测也靠它把上一个用例的答案丢掉。门面
（`gateway_models/__init__.py`）按原路径把它们再导出一份：`gateway_models.reset_cache()`
照旧写得出来，`gateway_models._cache` 也在，重导出的是同一个 dict 对象，往它身上
`.clear()` 落得到这一处。`_cache_generation` 是唯一会被重新绑定的名字（`reset_cache`
里 `global` 加一，换的是对象），别名会指到旧那个上，所以不转发、只留在这里。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

# 网关侧答案的存活时间。15 秒是「同一个人连点两下」和「页面自己在轮询」之间的那
# 个位置：短到不会让人看到过期的上线状态，长到一次页面加载只问网关一遍。
_TTL_SECONDS = 15.0

_cache: dict[str, tuple[float, Any]] = {}

# 缓存的代际。`reset_cache()` 让它 +1，`_cached` 写回前比对自己发起读时的代际。要它
# 是因为一次读要走 `await loader()`，期间可能有写操作把缓存失效（`reset_cache`）：没有
# 这道闸，那次读会在写之后把**写前**的旧答案写回缓存，盖掉刚做的失效，于是最长 TTL
# 内页面显示写前状态，而审计已经记了 ok。代际是唯一能区分「这次读属于哪个时代」的东西。
_cache_generation = 0


def reset_cache() -> None:
    """清空网关答案的缓存。写操作之后调它，测试也靠它把上一个用例的答案丢掉。"""
    global _cache_generation
    _cache_generation += 1
    _cache.clear()


async def _cached(key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
    """读网关答案的缓存，过期就重问。loader 抛异常时什么都不缓存 —— 一次失败不
    应该被记成 15 秒的「答案」。

    写回前比对代际：若这次读期间发生过 `reset_cache()`，说明手上这份是失效前的旧
    答案，丢弃写回；仍照常返回给本次调用者 —— 我们手里没有比它更新的答案。
    """
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    generation = _cache_generation
    value = await loader()
    if generation == _cache_generation:
        _cache[key] = (time.monotonic(), value)
    return value
