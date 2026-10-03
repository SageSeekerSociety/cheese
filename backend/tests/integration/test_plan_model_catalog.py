"""Plan editors describe the same models and tiers as the model picker."""

import pytest

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import GatewayModel
from tests.conftest import seed_user
from tests.integration.conftest import post_project


@pytest.fixture
def catalog(monkeypatch, client):
    monkeypatch.setattr(settings, "platform_admin_handles", ["catalog-admin"])
    monkeypatch.setattr(settings, "agent_model", "catalog-standard")
    gateway_catalog.reset()

    class Gateway:
        async def models(self):
            return [
                GatewayModel("catalog-standard", "Standard Model", True, True),
                GatewayModel("catalog-premium", "Premium Model", True, True, "premium"),
                GatewayModel("price-only", "Price Only", False, True, "frontier"),
                GatewayModel("unpriced", "Unpriced", True, False, "premium"),
            ]

    client.portal.call(gateway_catalog.refresh, Gateway())
    yield
    gateway_catalog.reset()


def test_plan_catalog_includes_subscription_models_and_matches_picker(client, catalog):
    owner = {"Authorization": f"Bearer {seed_user(client, 'catalog-admin')}"}
    project = post_project(client, json={"name": "Catalog"}, headers=owner).json()[
        "data"
    ]
    response = client.get("/admin/plans/models", headers=owner)
    assert response.status_code == 200, response.text
    models = response.json()["data"]["models"]
    by_id = {m["id"]: m for m in models}
    assert by_id["sonnet"]["tier"] == "premium"
    assert by_id["fable"]["tier"] == "frontier"
    assert by_id["catalog-standard"]["tier"] == "included"
    assert by_id["catalog-premium"]["label"] == "Premium Model"
    assert "price-only" not in by_id and "unpriced" not in by_id
    picker = client.get(f"/projects/{project['id']}/default-model", headers=owner)
    assert picker.status_code == 200, picker.text
    expected = [
        {key: choice[key] for key in ("id", "label", "tier")}
        for choice in picker.json()["data"]["choices"]
    ]
    assert models == expected


def test_plan_model_catalog_is_admin_only(client, catalog):
    owner = {"Authorization": f"Bearer {seed_user(client, 'catalog-member')}"}
    for headers in (owner, {}):
        assert client.get("/admin/plans/models", headers=headers).status_code in (
            401,
            403,
        )
