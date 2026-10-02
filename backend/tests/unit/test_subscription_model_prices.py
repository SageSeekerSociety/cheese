"""Every Claude model a project can pick on the subscription has a price.

Subscription usage is charged at the gateway's model prices; a model the price
table does not know is recorded and charged nothing. The table is the
gateway's own ``config.yaml``, read back the way the backend reads the live
gateway (``/model/info``), so a model added to the subscription list without a
price shows up here rather than as free usage.
"""

from pathlib import Path

import httpx
import pytest
import yaml

from app.core.config import settings
from app.domain.agent.market import subscription_model_alias, subscription_model_ids
from app.domain.feature_stats import pricing

_CONFIG = Path(__file__).resolve().parents[3] / "deploy" / "gateway" / "config.yaml"


@pytest.mark.anyio
async def test_every_subscription_model_has_all_four_prices(monkeypatch):
    entries = yaml.safe_load(_CONFIG.read_text())["model_list"]

    def model_info(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/model/info"
        return httpx.Response(200, json={"data": entries})

    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "admin")
    pricing.forget()
    try:
        table = await pricing.model_rates(httpx.MockTransport(model_info))
    finally:
        pricing.forget()

    assert table is not None
    for mid in sorted(subscription_model_ids()):
        alias = subscription_model_alias(mid)
        assert alias in table, f"{mid} ({alias}) has no price"
        assert all(rate > 0 for rate in table[alias]), alias
    # The price-only entries never route: the gateway serves none of them.
    routed = {
        e["model_name"]
        for e in entries
        if (e.get("model_info") or {}).get("blocked") is not True
    }
    assert not routed & {subscription_model_alias(m) for m in subscription_model_ids()}
