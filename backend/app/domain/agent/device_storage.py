"""What a machine holds under the platform's two storage roots.

Read by room retirement (``topic.retire``) to find the homes and checkouts a
room left on each machine.
"""

from app.domain.agent.device_hub import DeviceHub, device_hub
from app.domain.agent.device_provider import DEVICE_HOME_ROOT, DEVICE_WORK_ROOT


async def list_device_storage(
    device_id: str, *, hub: DeviceHub | None = None
) -> list[tuple[str, str, str]]:
    """Every ``(kind, project, place)`` under both device storage roots,
    as the device's shell sees them — names only, nothing resolved.

    Raises ``DeviceOffline`` like ``exec`` does; the caller decides what an
    unreachable device means for its sweep. Lists with a shell loop rather than
    `find -printf`, which is GNU-only and a device may be a Mac."""
    hub = hub or device_hub
    script = (
        f'for root in "{DEVICE_HOME_ROOT}" "{DEVICE_WORK_ROOT}"; do '
        '(cd "$root" 2>/dev/null || exit 0; '
        # A project/place symlink may point into the device owner's other data.
        'for p in */*; do if [ -d "$p" ] && '
        '[ ! -L "${p%%/*}" ] && [ ! -L "$p" ]; then '
        'printf "%s\\t%s\\n" "${root##*/}" "$p"; fi; done); done'
    )
    result = await hub.exec(device_id, ["sh", "-lc", script], timeout=30)
    if result.get("exit") != 0 or result.get("truncated"):
        # The machine's own words: without them a failed listing on a warm
        # host said nothing a fix could start from (three on 2026-10-06).
        raise RuntimeError(
            "device storage listing failed or was truncated: "
            f"exit={result.get('exit')} truncated={bool(result.get('truncated'))} "
            f"stderr={str(result.get('stderr') or '')[-300:]!r}"
        )
    pairs: list[tuple[str, str, str]] = []
    for line in str(result.get("stdout") or "").splitlines():
        kind, tab, path = line.partition("\t")
        project, sep, place = path.partition("/")
        if kind in {"home", "work"} and tab and sep and project and place:
            pairs.append((kind, project, place))
    return pairs
