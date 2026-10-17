"""快照列的是**全部**注册端点，只有越过载荷上限才截断，并且把截掉多少说出来。"""

from app.domain.platform_stats.performance import performance_snapshot


def _routes(count: int) -> list[tuple[str, str]]:
    return [("GET", f"/api/v1/thing/{i}") for i in range(count)]


def test_every_registered_route_is_listed() -> None:
    """没有样本的端点也各占一行 —— 看板不是「最快的 12 条」。"""
    snap = performance_snapshot(routes_registered=_routes(20))

    assert {row["route"] for row in snap["routes"]} == {
        f"/api/v1/thing/{i}" for i in range(20)
    }
    assert snap["routes_registered"] == 20
    assert snap["routes_omitted"] == 0


def test_the_cap_truncates_and_reports_what_was_dropped() -> None:
    """上限之内的都留着；越过时截到 2000 行，并在 `routes_omitted` 里说出来。"""
    snap = performance_snapshot(routes_registered=_routes(2001))

    assert len(snap["routes"]) == 2000
    assert snap["routes_registered"] == 2001
    assert snap["routes_omitted"] == 1
