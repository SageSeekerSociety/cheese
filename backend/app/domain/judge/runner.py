"""在评测主机上跑一次评测的那个脚本（方案 §一次评测怎么跑）。

后端用设备连接把它派到评测主机上：``python3 -I -c <源码>``，任务从标准输入读进来
（一份 JSON），结果一份精简的 JSON 写到标准输出。发过去的源码是 ``compare``、
``container`` 和这个文件按这个顺序拼成的一整段（``dispatch.RUNNER_SRC``），所以这三份
都只用标准库、都不写 ``from __future__``；在后端里被 import 时，下面那句 ``if
__package__`` 才去取另外两份。主机上要有 docker 和 Python 3.10 以上（``tarfile`` 的
``filter="data"``）。

一次评测：

1. 代码包（base64 的 tar）用 ``filter="data"`` 解到一个临时目录 —— 绝对路径、``..``、
   指到外面的链接、设备文件都进不来；
2. 有编译命令就编译一次：一个全新的容器，代码那份拷贝挂成可写；
3. 每个用例一个全新的容器，代码只读挂进去，输入从标准输入喂；墙钟到 3 倍时限还没完
   就 ``docker kill``；标准输出写到主机上的文件里，超过 16 MB 就停下、判「输出超限」；
4. 容器退出后用 ``docker inspect`` 读它是不是被内存上限杀的、退出码是多少，再删掉它；
5. 在容器外按出题人选的比法比对（期望输出从没进过容器）；
6. 回一份精简的 JSON：连接器回传标准输出最多 1 MiB，所以每个用例的实际输出最多留
   4 KB，用例多时再按总量往下压；
7. 不论成败，临时目录在 ``finally`` 里删掉。

只认 ``kind = "IO"``（输入/输出用例）。别的形态（第 2 步的测试脚本、命令判分）回
``{"ok": false, "error": "unsupported"}``，由后端标「暂不支持」；接上时在 ``KINDS``
里加一项即可。
"""

import base64
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid

if __package__:
    # In the backend (tests, and the dispatcher reading this file's source).
    # On the judge host these names were defined by the two files sent ahead.
    from app.domain.judge.compare import same_output
    from app.domain.judge.container import (
        TIME_MARK,
        cpu_seconds,
        run_args,
        workdir_of,
    )

#: Statuses of one case, as the result rows store them.
OK = "ok"
WRONG = "wrong"
TIMEOUT = "timeout"
MEMORY = "memory"
RUNTIME_ERROR = "runtime_error"
OUTPUT_LIMIT = "output_limit"
COMPILE_ERROR = "compile_error"

#: A program's output past this is cut off and the case is 输出超限.
OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
#: What of a program's output, or of the compiler's, goes back.
SHOWN_BYTES = 4096
#: All the actual outputs together stay under this, so the reply fits in the
#: connector's 1 MiB stdout with room for everything else.
REPLY_BUDGET = 768 * 1024
#: The wall clock a case may run: this many times its time limit, plus the
#: time a container takes to start.
WALL_FACTOR = 3
STARTUP_S = 2.0
COMPILE_LIMIT_S = 60
COMPILE_MEMORY_MB = 1024
PULL_TIMEOUT_S = 300
DOCKER = "docker"
#: How often ``_run`` looks whether the container is done.
POLL_S = 0.05


class PlatformError(Exception):
    """The host could not run the judging at all (no docker, no image). The
    backend tries again elsewhere; it is never the student's fault."""


def _docker(*args, timeout=60):
    try:
        return subprocess.run(
            [DOCKER, *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        why = f"docker {args[0]}: {exc}"
        raise PlatformError(why) from exc


def _ensure_image(image):
    if _docker("image", "inspect", image).returncode == 0:
        return
    pulled = _docker("pull", image, timeout=PULL_TIMEOUT_S)
    if pulled.returncode:
        why = f"docker pull {image}: {pulled.stderr.strip()[:500]}"
        raise PlatformError(why)


def _unpack(code_b64, into):
    """The code archive into ``into``, opened up for uid 1000: the compile
    container writes next to the sources."""
    data = base64.b64decode(code_b64 or "")
    os.makedirs(into)
    if data:
        if not hasattr(tarfile, "data_filter"):
            why = "this Python has no tarfile data filter"
            raise PlatformError(why)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            archive.extractall(into, filter="data")
    os.chmod(into, 0o777)
    for root, dirs, files in os.walk(into):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o777)
        for name in files:
            path = os.path.join(root, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISREG(mode):
                os.chmod(path, (mode & 0o111) | 0o666)


def _remove(path, image):
    """Remove the work directory. What a compile wrote belongs to uid 1000;
    if the host user cannot remove it, a container of that uid opens it up."""
    shutil.rmtree(path, ignore_errors=True)
    if not os.path.exists(path):
        return
    code = os.path.join(path, "code")
    if image and os.path.isdir(code):
        _docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "1000:1000",
            "--mount",
            f"type=bind,source={code},target=/w",
            image,
            "chmod",
            "-R",
            "a+rwX",
            "/w",
        )
    shutil.rmtree(path, ignore_errors=True)


class Ran:
    """What one container did."""

    def __init__(self):
        self.exit_code = None
        self.oom = False
        self.timed_out = False
        self.truncated = False
        self.stderr_head = b""
        self.stderr_tail = b""
        self.wall_s = None
        self.cpu_s = None
        self.memory_kb = None


def _pump_stdout(stream, sink, ran, over):
    """Copy stdout into ``sink`` up to the output limit, then keep draining.

    Past the limit it only says so (``over``); ``_run`` does the killing. It
    must never stop reading: docker does not let a killed container exit while
    its stdout is unread, so a ``docker kill`` waited on here never returns."""
    written = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return
        if ran.truncated:
            continue
        room = OUTPUT_LIMIT_BYTES - written
        if len(chunk) > room:
            sink.write(chunk[: max(room, 0)])
            ran.truncated = True
            over.set()
            continue
        sink.write(chunk)
        written += len(chunk)


def _pump_stderr(stream, ran):
    head = bytearray()
    tail = bytearray()
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if len(head) < SHOWN_BYTES:
            head += chunk[: SHOWN_BYTES - len(head)]
        tail += chunk
        del tail[:-SHOWN_BYTES]
    ran.stderr_head, ran.stderr_tail = bytes(head), bytes(tail)


def _feed(stream, data):
    try:
        stream.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _read_time(ran):
    """GNU time's line, the last of stderr: wall, user, system, peak KB."""
    for line in reversed(ran.stderr_tail.decode("utf-8", "replace").splitlines()):
        if line.startswith(TIME_MARK):
            fields = line.split()
            try:
                ran.wall_s = float(fields[1])
                ran.cpu_s = float(fields[2]) + float(fields[3])
                ran.memory_kb = int(fields[4])
            except (IndexError, ValueError):
                pass
            return


def _run(args, name, stdin, wall_s, out_path):
    """Run one container to its end; its stdout lands in ``out_path``."""
    ran = Ran()
    try:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        why = f"docker run: {exc}"
        raise PlatformError(why) from exc
    over = threading.Event()
    with open(out_path, "wb") as sink:
        threads = [
            threading.Thread(target=_feed, args=(process.stdin, stdin)),
            threading.Thread(
                target=_pump_stdout, args=(process.stdout, sink, ran, over)
            ),
            threading.Thread(target=_pump_stderr, args=(process.stderr, ran)),
        ]
        for thread in threads:
            thread.daemon = True
            thread.start()
        deadline = time.monotonic() + wall_s
        while process.poll() is None and not over.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                ran.timed_out = True
                break
            over.wait(min(left, POLL_S))
        if process.poll() is None:
            # Over the wall clock or the output limit. The pumps keep
            # draining while docker kills it.
            _docker("kill", name, timeout=30)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for thread in threads:
            thread.join(timeout=30)
    found = _docker("inspect", "--format", "{{json .State}}", name)
    _docker("rm", "--force", name, timeout=60)
    if found.returncode:
        # No container: docker could not start it (no daemon, no image, a
        # bad mount). That is the host's failure, not the program's.
        detail = ran.stderr_head.decode("utf-8", "replace").strip()
        why = f"docker run: {detail[:500] or found.stderr.strip()}"
        raise PlatformError(why)
    try:
        state = json.loads(found.stdout or "{}")
    except ValueError:
        state = {}
    ran.oom = bool(state.get("OOMKilled"))
    ran.exit_code = state.get("ExitCode", process.returncode)
    _read_time(ran)
    return ran


def _text(data, limit):
    return data[:limit].decode("utf-8", "replace")


def _stderr_text(ran):
    lines = [
        line
        for line in ran.stderr_head.decode("utf-8", "replace").splitlines()
        if not line.startswith(TIME_MARK)
    ]
    return "\n".join(lines)


def _verdict(ran, time_limit_ms, memory_mb):
    if ran.oom:
        return MEMORY
    if ran.timed_out:
        return TIMEOUT
    if ran.cpu_s is not None and ran.cpu_s * 1000 > time_limit_ms:
        return TIMEOUT
    if ran.truncated:
        return OUTPUT_LIMIT
    if ran.exit_code:
        if ran.memory_kb is not None and ran.memory_kb >= memory_mb * 1024 * 0.95:
            return MEMORY
        return RUNTIME_ERROR
    return None


def _compile(job, image, code, work):
    """Compile once; ``None`` when it built, else what the compiler said."""
    name = f"cheese-judge-{uuid.uuid4().hex}"
    ran = _run(
        run_args(
            name=name,
            image=image,
            code_dir=code,
            workdir=workdir_of(job.get("codeDir") or ""),
            command="umask 0000 && (" + job["compileCmd"] + ")",
            memory_mb=COMPILE_MEMORY_MB,
            cpu_limit_s=COMPILE_LIMIT_S,
            code_writable=True,
        ),
        name,
        b"",
        COMPILE_LIMIT_S + STARTUP_S,
        os.path.join(work, "compile.out"),
    )
    if not (ran.oom or ran.timed_out or ran.exit_code):
        return None
    with open(os.path.join(work, "compile.out"), "rb") as out:
        said = out.read(SHOWN_BYTES).decode("utf-8", "replace")
    said = (said + "\n" + _stderr_text(ran)).strip()
    if ran.timed_out:
        said = (said + "\n[compile timed out]").strip()
    elif ran.oom:
        said = (said + "\n[compile ran out of memory]").strip()
    return said[:SHOWN_BYTES]


def _case(job, image, code, work, case, shown):
    name = f"cheese-judge-{uuid.uuid4().hex}"
    limit_ms = int(job["timeLimitMs"])
    memory_mb = int(job["memoryMb"])
    out_path = os.path.join(work, "case.out")
    ran = _run(
        run_args(
            name=name,
            image=image,
            code_dir=code,
            workdir=workdir_of(job.get("codeDir") or ""),
            command=job["runCmd"],
            memory_mb=memory_mb,
            cpu_limit_s=cpu_seconds(limit_ms),
        ),
        name,
        str(case.get("input") or "").encode(),
        WALL_FACTOR * limit_ms / 1000 + STARTUP_S,
        out_path,
    )
    with open(out_path, "rb") as out:
        actual = out.read()
    os.remove(out_path)
    status = _verdict(ran, limit_ms, memory_mb)
    if status is None:
        status = (
            OK
            if same_output(
                actual,
                str(case.get("expected") or "").encode(),
                str(job.get("compare") or ""),
                float(job.get("tolerance") or 0.0),
            )
            else WRONG
        )
    ms = ran.cpu_s if ran.cpu_s is not None else ran.wall_s
    entry = {
        "index": case.get("index"),
        "status": status,
        "ms": int(ms * 1000) if ms is not None else None,
        "memoryKb": ran.memory_kb,
        "actual": _text(actual, shown),
    }
    if status == RUNTIME_ERROR:
        entry["stderr"] = _stderr_text(ran)[:shown]
    return entry


def judge_io(job, work):
    """Input/output cases: compile once, then one container per case."""
    image = job["image"]
    code = os.path.join(work, "code")
    _unpack(job.get("code"), code)
    _ensure_image(image)
    cases = list(job.get("cases") or [])
    shown = max(256, min(SHOWN_BYTES, REPLY_BUDGET // max(len(cases), 1)))
    workdir = workdir_of(job.get("codeDir") or "")
    if not os.path.isdir(os.path.join(code, os.path.relpath(workdir, "/code"))):
        said = f"code directory not found: {job.get('codeDir') or '.'}"
        return {
            "ok": True,
            "compile": {"status": RUNTIME_ERROR, "output": said},
            "cases": [
                {"index": c.get("index"), "status": RUNTIME_ERROR} for c in cases
            ],
        }
    if job.get("compileCmd"):
        failed = _compile(job, image, code, work)
        if failed is not None:
            return {
                "ok": True,
                "compile": {"status": COMPILE_ERROR, "output": failed},
                "cases": [
                    {"index": c.get("index"), "status": COMPILE_ERROR} for c in cases
                ],
            }
    return {
        "ok": True,
        "compile": {"status": OK, "output": ""},
        "cases": [_case(job, image, code, work, case, shown) for case in cases],
    }


#: Each kind of suite the runner knows how to judge. The second step's
#: test scripts and grading commands join here.
KINDS = {"IO": judge_io}


def judge(job):
    """One judging, start to end. Never raises: every way it can end is a reply."""
    handler = KINDS.get(str(job.get("kind") or ""))
    if handler is None:
        return {"ok": False, "error": "unsupported", "detail": str(job.get("kind"))}
    started = time.monotonic()
    work = tempfile.mkdtemp(prefix="cheese-judge-")
    try:
        reply = handler(job, work)
    except PlatformError as exc:
        reply = {"ok": False, "error": "platform", "detail": str(exc)[:SHOWN_BYTES]}
    except Exception as exc:  # anything else is the host's failure too
        reply = {
            "ok": False,
            "error": "platform",
            "detail": f"{type(exc).__name__}: {exc}"[:SHOWN_BYTES],
        }
    finally:
        _remove(work, job.get("image"))
    reply["machineMs"] = int((time.monotonic() - started) * 1000)
    return reply


def main():
    try:
        job = json.loads(sys.stdin.read())
    except ValueError as exc:
        reply = {"ok": False, "error": "platform", "detail": f"bad job: {exc}"}
    else:
        reply = judge(job if isinstance(job, dict) else {})
    sys.stdout.write(json.dumps(reply, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
