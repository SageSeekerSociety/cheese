"""Keep root diffs in the existing private backup bucket, one per session."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.storage import private_storage
from app.domain.machine.holder import namespace_of
from app.domain.machine.models import SandboxHome, SandboxRootSnapshot

PREFIX = "sandbox-root-snapshots"
URL_TTL_S = 3600
SAVE_TIMEOUT_S = 1500
RESTORE_TIMEOUT_S = 1500

NOTICES = {
    "restored": "已恢复这个会话保存的系统软件包和根目录改动。",
    "image_changed": "基础系统镜像已更新，保存的根目录改动未恢复。请重新安装需要的软件包。",
    "too_large": "上次根目录改动超过备份上限（压缩后 512 MiB、文件内容合计 2 GiB 或 200000 项），未保存。请重新安装需要的软件包。",
    "absent": "上次环境没有可保存的根目录改动。",
}


async def save(db, home, device_id, *, hub, run_on_host):
    """Stop the source, send its upper, verify the object, then replace its pointer.

    A failed transfer raises: idle deletion must retain the only copy and retry.
    An oversized diff records that fact instead of silently restoring older data.
    """
    if not home.sandbox_image or home.session_id is None or home.host_id is None:
        return None
    home_id, session_id, stopped_at = home.id, home.session_id, home.stopped_at
    bucket = private_storage()
    key = f"{PREFIX}/{home.session_id}/{int(datetime.now(UTC).timestamp())}-{uuid.uuid4()}.tar.gz"
    url = await bucket.presign(key, "put_object", URL_TTL_S)
    answer = await run_on_host(
        hub,
        device_id,
        "snapshot_root",
        timeout=SAVE_TIMEOUT_S,
        project=str(namespace_of(home)),
        resource=home.resource_id,
        image=home.sandbox_image,
        url=url,
    )
    if answer["status"] == "absent":
        # A home whose executor never started has no newer root to save.
        return answer
    if answer["status"] == "saved":
        stored = await bucket.stat(key)
        if stored != (answer["size"], answer["md5"]):
            # i18n-exempt: storage integrity diagnostic in backend logs
            raise RuntimeError(
                f"root snapshot verification failed: bucket reports {stored}, sent {(answer['size'], answer['md5'])}"
            )
    # A switch may finish while the upload is in flight. Its old source must
    # never replace (or delete) a newer home's backup. Hold only this short
    # publication transaction under the source row lock, never the transfer.
    source = await db.get(
        SandboxHome, home_id, with_for_update=True, populate_existing=True
    )
    if source is None or (source.session_id, source.stopped_at) != (
        session_id,
        stopped_at,
    ):
        await db.rollback()
        return {"status": "superseded"}
    row = await db.get(SandboxRootSnapshot, session_id, populate_existing=True)
    old_key = row.key if row else None
    if row is None:
        row = SandboxRootSnapshot(session_id=session_id)
        db.add(row)
    row.image, row.status = home.sandbox_image, answer["status"]
    row.key = key if answer["status"] == "saved" else None
    row.size, row.md5 = answer.get("size"), answer.get("md5")
    await db.commit()
    if old_key:
        await bucket.delete(old_key)
    return answer


async def for_restore(db, session_id):
    row = await db.get(SandboxRootSnapshot, session_id)
    if row is None:
        return None
    if row.status != "saved":
        return {"status": row.status}
    return {
        "image": row.image,
        "size": row.size,
        "md5": row.md5,
        "url": await private_storage().presign(row.key, "get_object", URL_TTL_S),
    }


async def sweep(sessions):
    """Retire deleted sessions and interrupted uploads after a day of grace.

    A fresh upload is not yet referenced by its database row. The grace period
    exceeds the signed URL and save deadlines, so the sweep cannot collect it.
    """
    try:
        bucket = private_storage()
    except RuntimeError:
        return 0
    cutoff = datetime.now(UTC).timestamp() - 86400
    removed = 0
    async for key in bucket.list_keys(PREFIX + "/"):
        saved_at = int(key.rsplit("/", 1)[-1].split("-", 1)[0])
        if saved_at > cutoff:
            continue
        async with sessions() as db:
            live = await db.scalar(
                select(SandboxRootSnapshot.session_id).where(
                    SandboxRootSnapshot.key == key
                )
            )
        if live is None:
            await bucket.delete(key)
            removed += 1
    return removed
