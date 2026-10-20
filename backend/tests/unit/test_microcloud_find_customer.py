"""The platform's MicroCloud customer must be found however many customers the
tenant has; a miss makes the caller register a fresh, separately funded one."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.domain.machine import microcloud

pytestmark = pytest.mark.anyio

REF = "cheese-platform-host-pool"


def contract_server(customers):
    """GET /customer as MicroCloud-API.yml has it: ordered by id, a cursor
    (`page_start`) to the first id of a page, `page_size` defaulting to 20, and
    an `external_ref` filter that matches by substring."""

    def respond(request):
        assert request.method == "GET"
        assert urlparse(str(request.url)).path == "/microcloud/customer"
        query = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        rows = sorted(customers, key=lambda c: c["id"])
        if query.get("external_ref"):
            rows = [c for c in rows if query["external_ref"] in c["externalRef"]]
        if "page_start" in query:
            rows = [c for c in rows if c["id"] >= int(query["page_start"])]
        size = int(query.get("page_size", 20))
        page, rest = rows[:size], rows[size:]
        return httpx.Response(
            200,
            json={
                "items": page,
                "page": {
                    "pageSize": size,
                    "hasPrev": "page_start" in query,
                    "hasMore": bool(rest),
                    "nextStart": rest[0]["id"] if rest else None,
                },
            },
        )

    return respond


async def test_finds_the_customer_past_the_first_page(monkeypatch):
    customers = [
        {"id": i, "externalRef": f"cheese-project:{i}", "status": "active"}
        for i in range(1, 301)
    ]
    # Refs that contain the platform's own ref are not it.
    customers += [
        {"id": i, "externalRef": f"{REF}-archive-{i}", "status": "active"}
        for i in range(301, 451)
    ]
    customers.append({"id": 602, "externalRef": REF, "status": "active"})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        microcloud.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(contract_server(customers)), **kwargs
        ),
    )
    client = microcloud.MicroCloudClient(
        base_url="https://fixture.invalid", secret="fixture"
    )

    found = await client.find_customer(REF)

    assert found is not None and found["id"] == 602
    assert await client.find_customer("cheese-platform-unknown") is None
