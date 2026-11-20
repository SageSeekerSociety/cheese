"""评测容器怎么起：一次性、断网、限时限内存（方案 §评测执行器、§安全）。

照私聊那套一次性容器（``agent/harness/claude_code/remote_execution/private.py`` 的
``run_command``）：只读根目录、去掉全部特权、禁止提权、uid 1000、内存连同 swap 一起
限死、一个核、进程数上限挡住分叉炸弹、不共享 IPC、不留日志，唯一可写的是 256 MB 的
``/tmp``。和私聊不同的三处：

* ``--network none`` —— 评测的容器一个网口都没有，私聊那种经代理出网的路也没有；
* ``--ulimit cpu`` —— CPU 时间到了内核就杀，用例的时限按 CPU 时间算；墙钟另由
  ``runner`` 盯着（3 倍时限，到点 ``docker kill``），挡住只睡不算的程序；
* 代码目录挂进去是只读的。编译那一步例外：编译产物要写回代码旁边（``g++ -o main``、
  ``javac``），所以编译用的容器把代码那份拷贝挂成可写，跑用例时再只读挂同一份。

期望输出从不进容器：``runner`` 把输入从标准输入喂进去，标准输出收回来，在容器外比。

和 ``compare``、``runner`` 一起被原样发到评测主机上（见 ``runner`` 开头），所以只用
标准库，不写 ``from __future__``。
"""

import math

#: Every judge container carries this label, so a host can find what is left.
LABEL = "com.cheese.judge"
#: Where the code is mounted inside the container.
CODE = "/code"
#: The one writable place: tmpfs, gone with the container.
SCRATCH_SIZE = "256m"
PIDS_LIMIT = 64
#: GNU time prints this line on stderr after the program exits: wall seconds,
#: user and system CPU seconds, peak resident memory in KB.
TIME_MARK = "__cheese_judge_time__"
TIME_FORMAT = TIME_MARK + " %e %U %S %M"
#: Where a test script writes its JUnit XML report: a directory of the host
#: bound in writable, empty when the run starts, read by the runner after the
#: container is gone. ``$JUDGE_REPORT`` names the file inside it.
REPORT_DIR = "/judge-report"
REPORT_FILE = REPORT_DIR + "/junit.xml"


def cpu_seconds(time_limit_ms: int) -> int:
    """The CPU-time ulimit for a case whose limit is ``time_limit_ms``: the
    limit rounded up to a whole second. The verdict reads the measured CPU time
    against the exact limit; the ulimit only makes sure a spinning program is
    stopped soon after it."""
    return max(1, math.ceil(time_limit_ms / 1000))


def run_args(
    *,
    name: str,
    image: str,
    code_dir: str,
    workdir: str,
    command: str,
    memory_mb: int,
    cpu_limit_s: int,
    code_writable: bool = False,
    mounts: tuple = (),
    env: tuple = (),
) -> list[str]:
    """``docker run`` for one compile or one test case: a brand-new container
    that reads stdin, runs ``command`` under ``sh -c`` in ``workdir`` (inside
    the code mount) and is gone after it.

    ``mounts`` are more writable binds ``(host dir, container dir)`` — the
    test scripts' report directory (``REPORT_DIR``) — and ``env`` more
    ``(name, value)`` variables. Neither loosens anything else: no network,
    the same uid, the same limits."""
    mount = f"type=bind,source={code_dir},target={CODE}"
    if not code_writable:
        mount += ",readonly"
    extra = []
    for key, value in env:
        extra += ["--env", f"{key}={value}"]
    for source, target in mounts:
        extra += ["--mount", f"type=bind,source={source},target={target}"]
    return [
        "docker",
        "run",
        "--interactive",
        "--init",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"{LABEL}=1",
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        f"{memory_mb}m",
        "--memory-swap",
        f"{memory_mb}m",
        "--cpus",
        "1",
        "--pids-limit",
        str(PIDS_LIMIT),
        "--ipc",
        "none",
        "--log-driver",
        "none",
        "--network",
        "none",
        "--ulimit",
        f"cpu={cpu_limit_s}:{cpu_limit_s + 1}",
        "--ulimit",
        "core=0",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,size={SCRATCH_SIZE},uid=1000,gid=1000,mode=0700",
        "--env",
        "HOME=/tmp",
        "--env",
        "LANG=C.UTF-8",
        *extra,
        "--mount",
        mount,
        "--workdir",
        workdir,
        image,
        "/usr/bin/time",
        "-f",
        TIME_FORMAT,
        "sh",
        "-c",
        command,
    ]


def workdir_of(code_subdir: str) -> str:
    """The container path of the setter's code directory (``""`` is the
    repository root). Anything that would climb out of the mount stays at
    its root."""
    parts = [p for p in code_subdir.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        return CODE
    return "/".join([CODE, *parts])
