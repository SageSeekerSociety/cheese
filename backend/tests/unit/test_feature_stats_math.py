"""分位数、分布描述与直方图的箱子，以及花费估算的单价规则。

这几件事都是纯算术，所以在这里算而不是在集成库里 —— 集成用例要造 90 天的行才
看得出来「p90 差一档」，而这一层一个列表就够。集成那一边（`test_feature_stats`）
钉的是**这些数从哪些行里来**，两边合起来才是完整的一句话。
"""

import httpx
import pytest

from app.domain.feature_stats import pricing, stats

# ---------- 分位数 ----------


def test_percentile_matches_percentile_cont() -> None:
    """线性插值，和 PostgreSQL 的 `percentile_cont` 同一个答案。

    最近秩（nearest-rank）在五个点上给得出 4000，而 SQL 给 4600 —— 差的那一档
    会让「90% 在这以内」和别的页面的同一个数对不上，而且没有人看得出来。
    """
    values = [1000, 2000, 3000, 4000, 5000]
    assert stats.percentile(values, 0.9) == pytest.approx(4600.0)
    assert stats.percentile(values, 0.5) == pytest.approx(3000.0)
    assert stats.percentile(values, 0.0) == pytest.approx(1000.0)
    assert stats.percentile(values, 1.0) == pytest.approx(5000.0)


def test_percentile_does_not_care_about_order() -> None:
    assert stats.percentile([5000, 1000, 3000, 2000, 4000], 0.5) == 3000.0


def test_one_value_is_its_own_every_quantile() -> None:
    assert stats.percentile([7], 0.9) == 7.0
    assert stats.percentile([7], 0.0) == 7.0


def test_no_values_is_none_and_never_zero() -> None:
    """「没有数据」不是 0 —— 页面上那两个记号长得必须不一样。"""
    assert stats.percentile([], 0.5) is None
    described = stats.describe([])
    assert described == {
        "count": 0,
        "avg": None,
        "median": None,
        "min": None,
        "p90": None,
        "max": None,
    }


def test_describe_reports_the_five_numbers() -> None:
    described = stats.describe([1000, 2000, 3000, 4000, 5000])
    assert described["count"] == 5
    assert described["avg"] == pytest.approx(3000.0)
    assert described["median"] == pytest.approx(3000.0)
    assert described["min"] == pytest.approx(1000.0)
    assert described["max"] == pytest.approx(5000.0)


# ---------- 直方图 ----------


def test_histogram_bins_cover_zero_to_max_and_keep_every_value() -> None:
    values = list(range(0, 1000, 100))
    bins = stats.histogram(values, bins=10)
    assert len(bins) == 10
    assert bins[0]["from"] == 0
    assert bins[-1]["to"] == pytest.approx(900.0)
    assert sum(bin_["count"] for bin_ in bins) == len(values)


def test_the_maximum_lands_in_the_last_bin() -> None:
    """上边界是闭的：最后一个值不能掉在图的右边、谁也数不到。"""
    bins = stats.histogram([0, 99, 100], bins=2)
    assert sum(bin_["count"] for bin_ in bins) == 3
    assert bins[-1]["count"] == 2


def test_histogram_of_nothing_is_empty_not_a_row_of_zeros() -> None:
    assert stats.histogram([]) == []


def test_histogram_of_zeros_is_one_bin() -> None:
    bins = stats.histogram([0, 0, 0])
    assert len(bins) == 1
    assert bins[0]["count"] == 3


# ---------- 花费估算 ----------


async def test_tokens_are_priced_with_the_rates_given() -> None:
    out = await pricing.estimate(
        {"deepseek-flash": (1_000_000, 500_000)},
        rates={"deepseek-flash": (0.0000002, 0.0000008)},
    )
    assert out["source"] == "estimated"
    assert out["usd"] == pytest.approx(1_000_000 * 0.0000002 + 500_000 * 0.0000008)
    assert out["unpriced_tokens"] == 0


async def test_tokens_of_an_unpriced_model_are_counted_and_declared() -> None:
    """没价的那部分单独报出来：整单少算一块，比一个「大概」更该让人看见。"""
    out = await pricing.estimate(
        {"deepseek-flash": (100, 100), "no-such-model": (50, 50)},
        rates={"deepseek-flash": (0.001, 0.002)},
    )
    assert out["usd"] == pytest.approx(100 * 0.001 + 100 * 0.002)
    assert out["unpriced_tokens"] == 100


async def test_no_rates_at_all_is_no_number_not_zero() -> None:
    out = await pricing.estimate(
        {"deepseek-flash": (100, 100)}, rates=None, transport=_dead_gateway()
    )
    assert out["usd"] is None
    assert out["source"] == "unavailable"
    assert out["unpriced_tokens"] == 200


def _dead_gateway() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    return httpx.MockTransport(handler)


def test_rates_read_both_places_litellm_keeps_them() -> None:
    """`model_info` 和 `litellm_params` 都是单价可能待的地方，网关两个都读。

    只读一个的实现会把另一种写法的模型判成无价，页面上就是「这个功能花的钱少了
    一大块」，而没有任何地方报错。
    """
    payload = {
        "data": [
            {
                "model_name": "in-params",
                "litellm_params": {
                    "input_cost_per_token": 0.001,
                    "output_cost_per_token": 0.002,
                },
            },
            {
                "model_name": "in-info",
                "model_info": {
                    "input_cost_per_token": 0.003,
                    "output_cost_per_token": 0.004,
                },
            },
            {
                # 只给了一半的价：整条丢掉，不能按半价算。
                "model_name": "half",
                "model_info": {"input_cost_per_token": 0.005},
            },
        ]
    }
    rates = pricing._rates_from_info(payload)
    assert rates["in-params"][:2] == (0.001, 0.002)
    assert rates["in-info"][:2] == (0.003, 0.004)
    assert "half" not in rates


def test_a_cached_token_with_no_price_of_its_own_costs_a_full_input_token() -> None:
    """网关没给缓存价的模型，缓存命中的 token 按输入价算：没写价不等于免费。
    给了缓存价（哪怕是 0）就按给的算。"""
    payload = {
        "data": [
            {
                "model_name": "no-cache-price",
                "litellm_params": {
                    "input_cost_per_token": 0.001,
                    "output_cost_per_token": 0.002,
                },
            },
            {
                "model_name": "cache-price",
                "litellm_params": {
                    "input_cost_per_token": 0.001,
                    "output_cost_per_token": 0.002,
                    "cache_read_input_token_cost": 0.00001,
                    "cache_creation_input_token_cost": 0,
                },
            },
        ]
    }
    rates = pricing._rates_from_info(payload)
    assert rates["no-cache-price"].cache_read == 0.001
    assert rates["no-cache-price"].cache_write == 0.001
    assert rates["cache-price"].cache_read == 0.00001
    assert rates["cache-price"].cache_write == 0
