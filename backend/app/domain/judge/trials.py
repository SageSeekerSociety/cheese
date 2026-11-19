"""出题人用参考答案试跑（方案 §出题人：配测试点）。

试跑在出题表单里点，题目可能还没发布，所以这一次评测不挂在题目和测试套上
（``task_id``、``suite_id`` 为空）：表单里那一套测试点的设置写进 ``trial_spec``，用例的
输入、期望输出和参考代码写进私有存储（``.judge-blobs/trials/<id>/...``，和测试用例一样
不进任何资料库），``space_id`` 记下算力记在哪个空间的所有者头上。之后它和学生提交的
评测走同一条队列、同一台评测主机、同一份计费。

三种形态都能试跑：测试脚本的测试文件、命令判分的判分文件与保护文件也写进同一处私有存储
（``test_files.store_files``），派出去时和题目里那一套走同一段代码（``dispatch._prepare``）。

参考代码两种给法：几份文本文件 ``[{path, content}]``，或一个小 zip（base64）。zip 里
的绝对路径、``..``、链接都不收。
"""

import base64
import io
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import blob_store
from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task import test_files
from app.domain.task.judge_queue import run_to_api
from app.domain.task.judging_models import TaskJudgeRun
from app.domain.task.test_files import FileLine
from app.domain.task.test_suite import IO, SuiteLine, suite_settings

#: A zip or a set of files the setter sends with one trial, before unpacking.
UPLOAD_MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 500
MAX_PATH_CHARS = 255


def _path(raw: object) -> str:
    text = str(raw or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text
        or len(text) > MAX_PATH_CHARS
        or path.is_absolute()
        or ".." in path.parts
        or text.endswith("/")
    ):
        raise BadRequestError(say("judgeTrialBadPath", path=text[:80]))
    return str(path)


def _tar(files: list[tuple[str, bytes]]) -> bytes:
    if not files:
        raise BadRequestError(say("judgeTrialNoCode"))
    if len(files) > MAX_FILES:
        raise BadRequestError(say("judgeTrialTooManyFiles", max=MAX_FILES))
    if sum(len(data) for _, data in files) > settings.judge_code_max_bytes:
        raise BadRequestError(
            say("judgeCodeTooLarge", mb=settings.judge_code_max_bytes // (1024 * 1024))
        )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def code_archive(files: list[dict] | None, zip_base64: str | None) -> bytes:
    """The reference code as the tar a judge host unpacks."""
    if zip_base64:
        try:
            raw = base64.b64decode(zip_base64, validate=True)
        except ValueError:
            raise BadRequestError(say("judgeTrialBadZip")) from None
        if len(raw) > UPLOAD_MAX_BYTES:
            raise BadRequestError(
                say("judgeTrialUploadTooLarge", mb=UPLOAD_MAX_BYTES // (1024 * 1024))
            )
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = [e for e in archive.infolist() if not e.is_dir()]
                if any((e.external_attr >> 16) & 0o170000 == 0o120000 for e in entries):
                    raise BadRequestError(say("judgeTrialBadZip"))
                if len(entries) > MAX_FILES:
                    raise BadRequestError(say("judgeTrialTooManyFiles", max=MAX_FILES))
                if sum(e.file_size for e in entries) > settings.judge_code_max_bytes:
                    raise BadRequestError(
                        say(
                            "judgeCodeTooLarge",
                            mb=settings.judge_code_max_bytes // (1024 * 1024),
                        )
                    )
                return _tar([(_path(e.filename), archive.read(e)) for e in entries])
        except zipfile.BadZipFile:
            raise BadRequestError(say("judgeTrialBadZip")) from None
    picked: list[tuple[str, bytes]] = []
    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").encode()
        picked.append((_path(entry.get("path")), content))
    if sum(len(data) for _, data in picked) > UPLOAD_MAX_BYTES:
        raise BadRequestError(
            say("judgeTrialUploadTooLarge", mb=UPLOAD_MAX_BYTES // (1024 * 1024))
        )
    return _tar(picked)


async def create_trial(
    session: AsyncSession,
    *,
    space_id: int,
    line: SuiteLine,
    code: bytes,
    user_id: int,
) -> TaskJudgeRun:
    """Queue one trial of ``line`` against ``code``; its bytes go to the
    private store first, and the row records where."""
    store = blob_store.for_new()
    base = f".judge-blobs/trials/{uuid.uuid4()}"
    cases: list[dict] = []
    for index, case in enumerate(line.cases):
        entry: dict = {
            "name": case.name,
            "points": case.points,
            "visibility": case.visibility,
            "testId": case.test_id,
        }
        if line.kind == IO:
            input_key, expected_key = f"{base}/{index}/in", f"{base}/{index}/out"
            await store.put(input_key, case.input.encode())
            await store.put(expected_key, case.expected.encode())
            entry.update(inputKey=input_key, expectedKey=expected_key)
        cases.append(entry)
    code_key = f"{base}/code.tar"
    await store.put(code_key, code)
    run = TaskJudgeRun(
        task_id=None,
        suite_id=None,
        suite_revision=0,
        space_id=space_id,
        purpose="TRIAL",
        trial_spec={
            "suite": suite_settings(line),
            "cases": cases,
            "files": await test_files.store_files(base, line.files),
            "location": store.location,
            "codeKey": code_key,
        },
        requested_by=user_id,
        state="queued",
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


@dataclass(frozen=True)
class Material:
    """What a trial judges: the suite's settings, its cases, the code."""

    suite: dict
    cases: list[dict]
    code: bytes
    files: list[FileLine]


async def material(spec: dict) -> Material:
    """A trial's settings, cases (with their bytes) and code, read back."""
    store = blob_store.store(str(spec.get("location") or ""))
    cases: list[dict] = []
    for index, case in enumerate(spec.get("cases") or []):
        entry = {
            "index": index,
            "id": None,
            "name": case.get("name") or "",
            "points": int(case.get("points") or 0),
            "visibility": case.get("visibility") or "SAMPLE",
            "testId": case.get("testId"),
        }
        if case.get("inputKey"):
            entry["input"] = (await store.get(case["inputKey"])).decode()
            entry["expected"] = (await store.get(case["expectedKey"])).decode()
        cases.append(entry)
    return Material(
        suite=dict(spec.get("suite") or {}),
        cases=cases,
        code=await store.get(str(spec.get("codeKey") or "")),
        files=await test_files.read_files(spec.get("files")),
    )


async def trial_of(
    session: AsyncSession, run_id: int, user_id: int
) -> TaskJudgeRun | None:
    """Trial ``run_id`` if ``user_id`` started it; None for anyone else, so
    another setter's trial does not exist for them."""
    run = await session.get(TaskJudgeRun, run_id)
    if run is None or run.purpose != "TRIAL" or run.requested_by != user_id:
        return None
    return run


def trial_to_api(run: TaskJudgeRun) -> dict:
    """A trial as its setter reads it: the judging with every case's input,
    expected and actual output, and how close the slowest case came to the
    time limit (方案：试跑会报出最慢的用例离时限还有多少余量)."""
    out = run_to_api(run, teacher=True) or {}
    suite = (run.trial_spec or {}).get("suite") or {}
    times = [
        int(entry["ms"])
        for entry in run.results or []
        if isinstance(entry, dict) and entry.get("ms") is not None
    ]
    return {
        **out,
        "runId": run.id,
        "timeLimitMs": int(suite.get("timeLimitMs") or 0),
        "slowestMs": max(times) if times else None,
    }
