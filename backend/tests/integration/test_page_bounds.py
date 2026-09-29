"""A page the server cannot serve is refused at the request, not sent to SQL.

Taken at face value, `pageStart=-1` becomes a negative OFFSET and
`pageSize=0` or a four-digit `pageSize` becomes a LIMIT that either errors
or pulls a whole table into memory. Both are now refused by the parameter
check; `validation_exception_handler` answers 400 `BadRequestError`, this
platform's shape for a bad request parameter.

`GET /recruitment` is the one paged list reachable without a session, so the
bounds can be stated without building anything first.
"""

from fastapi.testclient import TestClient


class TestPageBounds:
    def test_a_negative_page_start_is_refused(self, api_client: TestClient):
        resp = api_client.get("/recruitment", params={"pageStart": -1})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["name"] == "BadRequestError"

    def test_a_zero_page_size_is_refused(self, api_client: TestClient):
        resp = api_client.get("/recruitment", params={"pageSize": 0})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["name"] == "BadRequestError"

    def test_a_page_size_past_the_ceiling_is_refused(self, api_client: TestClient):
        resp = api_client.get("/recruitment", params={"pageSize": 101})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["name"] == "BadRequestError"

    def test_the_largest_page_the_server_offers_is_served(self, api_client: TestClient):
        resp = api_client.get("/recruitment", params={"pageSize": 100})
        assert resp.status_code == 200, resp.text

    def test_an_ordinary_page_is_served(self, api_client: TestClient):
        resp = api_client.get("/recruitment", params={"pageStart": 0, "pageSize": 20})
        assert resp.status_code == 200, resp.text

    def test_nothing_is_asked_and_the_default_page_is_served(
        self, api_client: TestClient
    ):
        resp = api_client.get("/recruitment")
        assert resp.status_code == 200, resp.text
