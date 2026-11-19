"""在评测主机上跑一次评测的那个脚本（方案 §一次评测怎么跑）。

后端用设备连接把它派到评测主机上：``python3 -I -c <源码>``，任务从标准输入读进来
（一份 JSON），结果一份精简的 JSON 写到标准输出。发过去的源码是 ``compare``、
``container``、``reports`` 和这个文件按这个顺序拼成的一整段（``script.RUNNER_SRC``），
所以这几份都只用标准库、都不写 ``from __future__``；在后端里被 import 时，下面那句
``if __package__`` 才去取另外几份。主机上要有 docker 和 Python 3.10 以上（``tarfile`` 的
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

测试脚本（``SCRIPT``）和命令判分（``COMMAND``）是另一种跑法（``judge_script``、
``judge_command``）：整轮只起**一个**容器，墙钟和 CPU 时间都按出题人给的整轮时限，代码
那份拷贝挂成可写（测试框架要写缓存、判分命令要写 score.json）；出题人的文件在起容器前
就放好（``_place``），学生的同名文件被覆盖。跑完读报告或分数（``reports``），读不出来
就回 ``scoreError``（一个短代码），后端标「判分失败」而不是 0 分（``judge.scoring``）。
不认得的形态回 ``{"ok": false, "error": "unsupported"}``，由后端标「暂不支持」。
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
        REPORT_DIR,
        REPORT_FILE,
        TIME_MARK,
        cpu_seconds,
        run_args,
        workdir_of,
    )
    from app.domain.judge.reports import (
        REPORT_MAX_BYTES,
        SCORE_JSON_MAX_BYTES,
        ReportError,
        parse_junit,
        parse_score_json,
        parse_score_line,
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


#: What a whole run's program output keeps for the teacher: this many first
#: lines, at most this many bytes. The student sees a head of it, if the setter
#: allows (``studentLogLines``).
LOG_LINES = 200
LOG_BYTES = 16 * 1024
#: A test's failure message is cut to this many bytes, less when there are many.
MESSAGE_BYTES = 1024
#: A file the setter's files are placed over; a student's own ``conftest.py``
#: outside them is removed before a test script runs (``_drop_conftests``).
CONFTEST = "conftest.py"
#: score.json, at the repository root.
SCORE_JSON = "score.json"


def _place(root, rel, data):
    """Write one of the setter's files at ``rel`` under ``root``, over whatever
    the student had there.

    Nothing the student put in the way is followed: a link or a plain file
    where a directory should be is removed, and so is a link or a directory
    where the file goes — writing through a student's link would land the
    setter's bytes somewhere else and leave the student's file in place."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        why = f"bad file path: {rel[:200]}"
        raise PlatformError(why)
    here = root
    for part in parts[:-1]:
        here = os.path.join(here, part)
        if os.path.islink(here) or (os.path.lexists(here) and not os.path.isdir(here)):
            os.remove(here)
        if not os.path.isdir(here):
            os.mkdir(here)
            os.chmod(here, 0o777)
    target = os.path.join(here, parts[-1])
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
    elif os.path.lexists(target):
        os.remove(target)
    with open(target, "wb") as out:
        out.write(data)
    os.chmod(target, 0o666)
    return "/".join(parts)


def _place_all(code, files):
    """Every file of the job in place; returns the paths written."""
    placed = set()
    for entry in files or []:
        data = base64.b64decode(str(entry.get("data") or ""))
        placed.add(_place(code, str(entry.get("path") or ""), data))
    return placed


def _drop_conftests(code, keep):
    """Remove every ``conftest.py`` the setter did not put there.

    pytest loads a ``conftest.py`` it finds on the way to the tests (the
    root, ``tests/``, any directory above a test file) before any test runs,
    and a hook in one can mark every test passed. ``--noconftest`` would drop
    the setter's own ``tests/conftest.py`` too, and it only helps when the
    setter remembers to pass it; removing the student's files works whatever
    the run command says. ``os.walk`` does not follow links, so a linked
    directory is not searched — and a linked ``conftest.py`` is removed like
    any other."""
    for root, _dirs, files in os.walk(code):
        for name in files:
            if name != CONFTEST:
                continue
            path = os.path.join(root, name)
            if os.path.relpath(path, code).replace(os.sep, "/") not in keep:
                os.remove(path)


def _head_lines(text, lines, limit):
    kept = "\n".join(text.splitlines()[:lines])
    return kept.encode()[:limit].decode("utf-8", "ignore")


def _whole_run(job, image, code, work, command, mounts=(), env=()):
    """One container for the whole run, under the suite's total time limit.
    Returns what it did, what it printed on stdout, and the log the teacher
    reads (the first lines of stdout, then of stderr)."""
    name = f"cheese-judge-{uuid.uuid4().hex}"
    total_s = int(job.get("totalTimeLimitS") or 120)
    out_path = os.path.join(work, "run.out")
    ran = _run(
        run_args(
            name=name,
            image=image,
            code_dir=code,
            workdir=workdir_of(job.get("codeDir") or ""),
            command=command,
            memory_mb=int(job["memoryMb"]),
            cpu_limit_s=total_s,
            code_writable=True,
            mounts=mounts,
            env=env,
        ),
        name,
        b"",
        total_s + STARTUP_S,
        out_path,
    )
    with open(out_path, "rb") as out:
        stdout = out.read().decode("utf-8", "replace")
    os.remove(out_path)
    log = stdout.rstrip("\n")
    stderr = _stderr_text(ran).strip()
    if stderr:
        log = (log + "\n" + stderr).strip("\n")
    return ran, stdout, _head_lines(log, LOG_LINES, LOG_BYTES)


def _stopped(ran):
    """Why the run was cut short, if it was: a limit, never the exit code."""
    if ran.oom:
        return MEMORY
    if ran.timed_out:
        return TIMEOUT
    if ran.truncated:
        return OUTPUT_LIMIT
    return None


def _whole_reply(ran, log):
    return {
        "ok": True,
        "stopped": _stopped(ran),
        "exitCode": ran.exit_code,
        "log": log,
    }


def _no_code_dir(job):
    return {
        "ok": True,
        "scoreError": "code_dir_missing",
        "stopped": None,
        "exitCode": None,
        "log": f"code directory not found: {job.get('codeDir') or '.'}",
    }


def _code_dir_exists(job, code):
    workdir = workdir_of(job.get("codeDir") or "")
    return os.path.isdir(os.path.join(code, os.path.relpath(workdir, "/code")))


def judge_script(job, work):
    """A test script: the setter's tests next to the student's code, one run
    command that writes a JUnit XML report to ``$JUDGE_REPORT``, every test
    read out of the report."""
    image = job["image"]
    code = os.path.join(work, "code")
    _unpack(job.get("code"), code)
    _ensure_image(image)
    if not _code_dir_exists(job, code):
        return _no_code_dir(job)
    placed = _place_all(code, job.get("files"))
    _drop_conftests(code, placed)
    report_dir = os.path.join(work, "report")
    os.makedirs(report_dir)
    os.chmod(report_dir, 0o777)
    ran, _, log = _whole_run(
        job,
        image,
        code,
        work,
        job["runCmd"],
        mounts=((report_dir, REPORT_DIR),),
        env=(("JUDGE_REPORT", REPORT_FILE),),
    )
    reply = _whole_reply(ran, log)
    report = os.path.join(report_dir, os.path.basename(REPORT_FILE))
    try:
        if not os.path.isfile(report) or os.path.islink(report):
            raise ReportError("report_missing")
        if os.path.getsize(report) > REPORT_MAX_BYTES:
            raise ReportError("report_too_large")
        with open(report, "rb") as found:
            data = found.read(REPORT_MAX_BYTES + 1)
        tests, truncated = parse_junit(data, MESSAGE_BYTES)
        if not tests:
            raise ReportError("report_empty")
    except ReportError as exc:
        reply["scoreError"] = exc.code
        return reply
    room = max(128, min(MESSAGE_BYTES, REPLY_BUDGET // 2 // max(len(tests), 1)))
    for test in tests:
        test["message"] = test["message"].encode()[:room].decode("utf-8", "ignore")
    reply["tests"] = tests
    reply["testsTruncated"] = truncated
    return reply


def _commands_script(commands):
    """The commands as one shell script that stops at the first that fails,
    saying which one did on stdout."""
    steps = []
    for number, command in enumerate(commands, 1):
        steps.append(
            "(\n" + command + "\n) || { s=$?; "
            f'echo "[command {number} exited $s]"; exit $s; }}'
        )
    return "\n".join(steps)


def judge_command(job, work):
    """Grading commands: the setter's protected and grader files put back,
    the commands run in order, the score read from the last ``SCORE: x/y``
    line or from score.json at the repository root."""
    image = job["image"]
    code = os.path.join(work, "code")
    _unpack(job.get("code"), code)
    _ensure_image(image)
    if not _code_dir_exists(job, code):
        return _no_code_dir(job)
    _place_all(code, job.get("files"))
    score_json = os.path.join(code, SCORE_JSON)
    # A score.json the student committed is not a score: only one the
    # commands write during this run counts.
    if os.path.isdir(score_json) and not os.path.islink(score_json):
        shutil.rmtree(score_json)
    elif os.path.lexists(score_json):
        os.remove(score_json)
    ran, stdout, log = _whole_run(
        job, image, code, work, _commands_script(job.get("commands") or [])
    )
    reply = _whole_reply(ran, log)
    try:
        if str(job.get("scoreSource") or "LINE") == "JSON":
            if not os.path.isfile(score_json) or os.path.islink(score_json):
                raise ReportError("score_missing")
            with open(score_json, "rb") as found:
                score = parse_score_json(found.read(SCORE_JSON_MAX_BYTES + 1))
        else:
            score = parse_score_line(stdout)
    except ReportError as exc:
        reply["scoreError"] = exc.code
        return reply
    reply["score"] = score
    return reply


#: Each kind of suite the runner knows how to judge.
KINDS = {"IO": judge_io, "SCRIPT": judge_script, "COMMAND": judge_command}


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
