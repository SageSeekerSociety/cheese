"""A rolling backend's hub answers every call the backend makes of a hub.

On a deployment with a connection owner, the `device_hub` the business backend
holds is `RemoteDeviceHub`; on one without, it is `DeviceHub`. Code is written
and tested against the second, and a method the first does not have fails only
on the first — at the call, as an `AttributeError`, which a caller that
reports device failures instead of raising them turns into "the machine
answered badly". Granting a local directory read that way on dev for every
grant: the remote hub had no `push_local_fs_grants`.

Testing each caller through the remote hub covers the callers that exist when
the test is written. This covers the next one: any public `DeviceHub` method
the business backend calls — under whatever name it holds the hub, which for
the grant push was `link` — has to exist on `RemoteDeviceHub` too.

What runs only inside the owner process is left out: the owner keeps the
in-process hub, and the connector routes (device and terminal sockets) are
mounted there alone.
"""

import ast
from pathlib import Path

import app
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub

APP = Path(app.__file__).parent
OWNER_ONLY = {
    APP / "device_connection_app.py",
    APP / "api" / "routes" / "connector.py",
    APP / "domain" / "agent" / "device_hub.py",
    APP / "domain" / "agent" / "device_hub_rpc.py",
}


def _hub_methods() -> set[str]:
    return {
        name
        for name, value in vars(DeviceHub).items()
        if callable(value) and not name.startswith("_")
    }


def _business_calls(names: set[str]) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if path in OWNER_ONLY:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in names
            ):
                calls.setdefault(node.func.attr, []).append(
                    f"{path.relative_to(APP.parent)}:{node.lineno}"
                )
    return calls


def test_the_remote_hub_has_every_hub_method_the_backend_calls() -> None:
    calls = _business_calls(_hub_methods())
    missing = {
        name: sites
        for name, sites in calls.items()
        if not callable(getattr(RemoteDeviceHub, name, None))
    }

    assert not missing, (
        "the business backend calls these DeviceHub methods, and RemoteDeviceHub "
        "(what it holds when a connection owner runs) does not have them; add "
        "each to RemoteDeviceHub and to the owner's calls in "
        f"device_connection_app.py: {missing}"
    )


def test_the_scan_sees_the_backend_calling_the_hub() -> None:
    # Without this, a scan that matched nothing would pass the test above.
    calls = _business_calls(_hub_methods())

    assert {"is_online", "open_screen", "push_local_fs_grants"} <= set(calls)
