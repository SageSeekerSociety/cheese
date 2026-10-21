"""Lookups against MicroCloud's lists must see the whole list and honour its
filters. A miss on the platform customer registers a fresh, separately funded
one; a miss on a reservation's machine drops a row whose machine still bills."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.domain.machine import microcloud

pytestmark = pytest.mark.anyio

REF = "cheese-platform-host-pool"


def contract_server(lists):
    """The tenant list endpoints as MicroCloud-API.yml has them: ordered by id,
    a cursor (`page_start`) that is the id of a page's first row, `page_size`
    defaulting to 20, an `external_ref` filter on /customer that matches by
    substring, and a `customer_id` filter on /account and /machine. Any other
    query parameter is ignored, as the server ignores it."""

    def respond(request):
        assert request.method == "GET"
        path = urlparse(str(request.url)).path.removeprefix("/microcloud")
        query = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        rows = sorted(lists[path], key=lambda r: r["id"])
        if path == "/customer" and query.get("external_ref"):
            rows = [r for r in rows if query["external_ref"] in r["externalRef"]]
        if path in ("/account", "/machine") and "customer_id" in query:
            rows = [r for r in rows if r["customerId"] == int(query["customer_id"])]
        if "page_start" in query:
            # The cursor names an existing id; one that names no row reads as
            # the end of the list, not as "from the next id on".
            ids = [r["id"] for r in rows]
            start = int(query["page_start"])
            rows = rows[ids.index(start) :] if start in ids else []
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


@pytest.fixture
def serve(monkeypatch):
    def install(**lists):
        real_client = httpx.AsyncClient
        routes = {"/" + name.replace("_", "/"): rows for name, rows in lists.items()}
        monkeypatch.setattr(
            microcloud.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(contract_server(routes)), **kwargs
            ),
        )
        return microcloud.MicroCloudClient(
            base_url="https://fixture.invalid", secret="fixture"
        )

    return install


async def test_finds_the_customer_past_the_first_page(serve):
    customers = [
        {"id": i, "externalRef": f"cheese-project:{i}", "status": "active"}
        for i in range(1, 301)
    ]
    # Refs that contain the platform's own ref are not it.
    customers += [
        {"id": i, "externalRef": f"{REF}-archive-{i}", "status": "active"}
        for i in range(301, 601, 2)
    ]
    customers.append({"id": 602, "externalRef": REF, "status": "active"})
    client = serve(customer=customers)

    found = await client.find_customer(REF)

    assert found is not None and found["id"] == 602
    assert await client.find_customer("cheese-platform-unknown") is None


async def test_finds_the_customers_machine_among_the_whole_tenants(serve):
    # Other customers' machines come first, more than a page of them, and one
    # of them carries the same hostname.
    machines = [
        {"id": i, "customerId": 9, "hostname": f"warm-{i}", "status": "running"}
        for i in range(1, 251)
    ]
    machines.append(
        {"id": 251, "customerId": 9, "hostname": "host-abc", "status": "running"}
    )
    machines.append(
        {"id": 400, "customerId": 2, "hostname": "host-abc", "status": "running"}
    )
    client = serve(machine=machines)

    found = await client.find_machine(2, "host-abc")

    assert found is not None and found["id"] == 400
    assert await client.find_machine(2, "host-missing") is None


async def test_finds_the_account_past_the_first_page(serve):
    accounts = [
        {"id": i, "customerId": 2, "name": f"project-{i}", "balance": 0}
        for i in range(1, 251)
    ]
    accounts.append({"id": 300, "customerId": 2, "name": "warm-pool", "balance": 5})
    accounts.append({"id": 301, "customerId": 3, "name": "other", "balance": 0})
    client = serve(account=accounts)

    found = await client.find_account(2, "warm-pool")

    assert found is not None and found["id"] == 300
    assert await client.find_account(2, "other") is None


async def test_lists_every_offering(serve):
    offerings = [{"id": i, "machineTypeId": 1, "zoneId": 1} for i in range(1, 251)]
    client = serve(machine_offering=offerings)

    listed = await client.list_offerings()

    assert sorted(o["id"] for o in listed) == list(range(1, 251))
