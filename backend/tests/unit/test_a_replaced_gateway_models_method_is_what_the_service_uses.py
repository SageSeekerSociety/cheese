"""换掉服务上的一个方法之后，它自己身子里的调用也换成那一个。

拆包把每个原先的 `self.X(...)` 换成了 `service.X(...)`：子模块经对象叫自己的方法，
而不是直接叫兄弟模块的函数。这不是写法偏好——这几处是合起来做一个决定的：网关可
达吗，先看管理 API 答不答（`_readiness`）；「这个模型现在什么样」，先把模型表要过
来（`_require_models`）；写完读回那一项，是把这一项交给 `_item` 摆成列表页的口径。
换掉 `_readiness`、`_require_models`、`_item` 就能让这几条路不碰真网关、不碰真库，
`_read_back` 那条还横跨 `_shared` 和 `models` 两个子模块。这是这套拆法要保住的东西：
子模块经对象调自己的方法，替换点才落得进服务内部。

要是哪个子模块图省事直接叫兄弟模块的函数（`_readiness(service)`），替换点就沉默地
失效：换掉方法的人只换到他自己那一次调用，服务内部照旧走旧的路，而别的测试照样绿。
拆成包时最容易悄悄丢掉的就是它。下面几条各钉一个替换点，`_read_back` 和 `_window`
那两条跨子模块。
"""

from datetime import date
from types import SimpleNamespace

from app.domain.agent.gateway_models import GatewayModelsService


def _service() -> GatewayModelsService:
    # 这几处替换点都在碰数据库和网关之前就分岔了，所以两者给个占位就行。
    return GatewayModelsService(SimpleNamespace(), SimpleNamespace())


async def test_gateway_up_asks_a_replaced_readiness():
    service = _service()
    asked: list[str] = []

    async def replaced() -> str:
        asked.append("readiness")
        return "ok"

    service._readiness = replaced

    await service._gateway_up()

    assert asked == ["readiness"]


async def test_current_model_takes_what_a_replaced_require_models_gives():
    service = _service()
    wanted = SimpleNamespace(name="gpt-5")

    async def replaced() -> list:
        return [wanted]

    service._require_models = replaced

    assert await service._current_model("gpt-5") is wanted


async def test_read_back_hands_the_item_to_a_replaced_item():
    """跨子模块的那条边：`_shared` 里的读回，摆盘交给 `models` 里的 `_item`。"""
    service = _service()
    model = SimpleNamespace(name="gpt-5", sources=[])

    async def models_raw() -> list:
        return [model]

    async def usage_window(start, end):
        return SimpleNamespace(by_model={})

    service._models_raw = models_raw
    service._usage_window = usage_window
    service._item = lambda model, usage, ledger=None: {"replaced": True}

    assert await service._read_back("gpt-5", {}, "model-id") == {"replaced": True}


def test_window_takes_the_day_a_replaced_today_gives():
    """`_window` 里的「今天」是问对象要的。

    原先 `_window` 调 `self._today()`；拆到 `_shared` 之后这里要是写成直接叫模块级
    的 `_today(service)`，替换点就断了——而别的测试照旧绿。
    """
    service = _service()
    service._today = lambda: date(2026, 9, 23)

    start, end, since, until = service._window(7)

    assert (start, end) == (date(2026, 9, 16), date(2026, 9, 23))
    assert since.date() == date(2026, 9, 16)
    assert until.date() == date(2026, 9, 24)
