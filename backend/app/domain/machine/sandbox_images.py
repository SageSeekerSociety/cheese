"""Prepare default images while hosts keep accepting on their ready image."""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.domain.agent import sandbox_image, toolchain
from app.domain.agent.device_hub import device_hub
from app.domain.agent.machine_address import device_api_base
from app.domain.machine.models import CloudHost, SandboxHome, WarmMachine

logger = logging.getLogger(__name__)


async def image_for_session(db, device_id, session_id):
    host = await db.scalar(select(CloudHost).where(CloudHost.device_id == device_id))
    if host is None or host.whole_machine:
        return None
    home = await db.scalar(
        select(SandboxHome).where(SandboxHome.session_id == session_id)
    )
    if home is None:
        return host.sandbox_image or None
    if not home.sandbox_image:
        home.sandbox_image = host.sandbox_image
        await db.flush()
    return home.sandbox_image or None


def preparation(reference, api, retained=()):
    artifacts = {
        arch: {
            "url": f"{api.rstrip('/')}/connector/toolchain/crane/{platform}/artifact",
            "sha256": toolchain.ARTIFACTS[("crane", platform)].sha256,
        }
        for arch, platform in (("x86_64", "linux-x64"), ("aarch64", "linux-arm64"))
    }
    return (
        [
            "sudo",
            "-n",
            "/usr/bin/python3",
            "-I",
            "-",
            reference,
            "--keep",
            str(sandbox_image.default_image()["keep_old"]),
            "--crane-artifacts",
            json.dumps(artifacts),
            "--retain",
            json.dumps(list(retained)),
        ],
        Path(sandbox_image.__file__).read_text(),
    )


async def sweep(sessions):
    reference = sandbox_image.default_image()["reference"]
    async with sessions() as db:
        hosts = list(
            await db.scalars(
                select(CloudHost).where(
                    CloudHost.released_at.is_(None),
                    CloudHost.whole_machine.is_(False),
                    CloudHost.device_id.is_not(None),
                    CloudHost.enrolled_at.is_not(None),
                    CloudHost.sandbox_image.is_distinct_from(reference),
                )
            )
        )
        warm = list(
            await db.scalars(
                select(WarmMachine).where(
                    WarmMachine.state == "ready",
                    WarmMachine.device_id.is_not(None),
                    WarmMachine.sandbox_image.is_distinct_from(reference),
                )
            )
        )
        targets = [(type(row), row.id, row.device_id) for row in [*hosts, *warm]]
    gate = asyncio.Semaphore(4)

    async def prepare(model, key, device):
        if not device_hub.is_online(device):
            return
        async with gate:
            try:
                async with sessions() as db:
                    api = await device_api_base(
                        db, device, settings.connector_public_base
                    )
                    retained = (
                        list(
                            await db.scalars(
                                select(SandboxHome.sandbox_image)
                                .where(
                                    SandboxHome.host_id == key,
                                    SandboxHome.sandbox_image.is_not(None),
                                )
                                .distinct()
                            )
                        )
                        if model is CloudHost
                        else []
                    )
                    row = await db.get(model, key)
                    if row is None or row.sandbox_image == reference:
                        return
                    if row is not None and row.sandbox_image:
                        retained.append(row.sandbox_image)
                argv, source = preparation(reference, api, retained)
                result = await device_hub.exec(
                    device, argv, stdin=source, timeout=sandbox_image.PREWARM_TIMEOUT_S
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    # i18n-exempt: caught below and logged by background maintenance
                    raise RuntimeError(
                        "image preparation failed: "
                        + (result.get("stderr") or "")[-500:]
                    )
                receipt = json.loads(result["stdout"].strip().splitlines()[-1])
                if receipt["reference"] != reference:
                    # i18n-exempt: caught below and logged by background maintenance
                    raise RuntimeError("host prepared a different image")
                async with sessions() as db:
                    row = await db.get(model, key)
                    if row is not None and row.sandbox_image != reference:
                        row.sandbox_image = reference
                        await db.commit()
            except Exception:
                logger.exception(
                    "cloud sandbox image preparation failed for %s", device
                )

    await asyncio.gather(*(prepare(*target) for target in targets))
