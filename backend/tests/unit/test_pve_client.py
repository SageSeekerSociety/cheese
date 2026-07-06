"""Unit tests for PveClient against a mock httpx transport (no live cluster)."""

from collections.abc import Callable

import httpx
import pytest

from app.compute.pool.pve_client import PveClient, PveError

pytestmark = pytest.mark.anyio


def _pve(handler: Callable[[httpx.Request], httpx.Response]) -> PveClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PveClient("https://pve:8006", "u@pve!tok", "secret", client=client)


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


def test_requires_credentials():
    with pytest.raises(PveError):
        PveClient("", "u", "s")


async def test_version_sends_token_and_path():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": {"version": "9.2"}})

    v = await _pve(handler).version()
    assert v["version"] == "9.2"
    assert seen["auth"] == "PVEAPIToken=u@pve!tok=secret"
    assert seen["path"] == "/api2/json/version"


async def test_list_guests_parses_template_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"vmid": 119, "node": "pve119", "type": "lxc", "name": "t", "template": 1, "status": "stopped"},
                    {"vmid": 200, "node": "pve25", "type": "qemu", "name": "vm", "status": "running"},
                ]
            },
        )

    guests = await _pve(handler).list_guests()
    assert {g.vmid for g in guests} == {119, 200}
    lxc = next(g for g in guests if g.vmid == 119)
    assert lxc.is_template and lxc.kind == "lxc"
    assert not next(g for g in guests if g.vmid == 200).is_template


async def test_next_vmid():
    assert await _pve(lambda r: httpx.Response(200, json={"data": "131"})).next_vmid() == 131


async def test_clone_lxc_uses_hostname_and_returns_upid():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _form(request)
        return httpx.Response(200, json={"data": "UPID:pve119:xxx"})

    upid = await _pve(handler).clone(
        node="pve119", vmid=119, newid=131, name="client-a", kind="lxc", pool="p"
    )
    assert upid == "UPID:pve119:xxx"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api2/json/nodes/pve119/lxc/119/clone"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["hostname"] == "client-a"
    assert body["newid"] == "131"
    assert body["pool"] == "p"


async def test_clone_qemu_uses_name():
    seen: dict[str, dict[str, str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _form(request)
        return httpx.Response(200, json={"data": "UPID:x"})

    await _pve(handler).clone(node="pve25", vmid=124, newid=132, name="vm-a", kind="qemu")
    assert seen["body"]["name"] == "vm-a"
    assert "hostname" not in seen["body"]


async def test_clone_rejects_bad_kind():
    pve = _pve(lambda r: httpx.Response(200, json={"data": "x"}))
    with pytest.raises(PveError):
        await pve.clone(node="n", vmid=1, newid=2, name="x", kind="bogus")


async def test_http_error_raises_pve_error():
    pve = _pve(lambda r: httpx.Response(403, text="permission denied"))
    with pytest.raises(PveError):
        await pve.version()


async def test_next_vmid_null_data_fails_closed():
    pve = _pve(lambda r: httpx.Response(200, json={"data": None}))
    with pytest.raises(PveError):
        await pve.next_vmid()


async def test_non_json_body_fails_closed():
    pve = _pve(lambda r: httpx.Response(200, text="not json at all"))
    with pytest.raises(PveError):
        await pve.version()


async def test_clone_null_upid_fails_closed():
    pve = _pve(lambda r: httpx.Response(200, json={"data": None}))
    with pytest.raises(PveError):
        await pve.clone(node="n", vmid=1, newid=2, name="x", kind="lxc")
