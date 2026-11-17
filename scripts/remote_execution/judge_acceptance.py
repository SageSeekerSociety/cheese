"""Hold the judge's container to its limits on a real docker (方案 §安全).

Each check sends the judge host's script (``app.domain.judge.script``) a job
whose student program tries to break one limit, exactly as a judge host runs
it (``python3 -I -c``), and fails unless the limit held:

* the network is unreachable;
* a fork bomb is stopped by the process limit;
* a program over its memory is judged out of memory;
* a program that sleeps is judged a timeout, near three times its limit;
* output past 16 MB is cut off and judged as over the output limit;
* the root, the code and the system directories cannot be written, /tmp can;
* the program runs as uid 1000 with no capabilities;
* the expected output is nowhere in the container for the program to read.

The image is the Python runtime's (``backend/sandbox/judge/Dockerfile.python``),
already loaded under the name ``app.domain.judge.runtimes`` gives it.

    backend/.venv/bin/python scripts/remote_execution/judge_acceptance.py \\
        --output tmp/judge-acceptance
"""

import argparse
import base64
import io
import json
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.domain.judge.runtimes import image_of  # noqa: E402
from app.domain.judge.script import RUNNER_SRC  # noqa: E402

IMAGE = image_of("python3.12")


def _code(source: str) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = source.encode()
        info = tarfile.TarInfo("main.py")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return base64.b64encode(buffer.getvalue()).decode()


def judge(source: str, *, time_limit_ms=1000, memory_mb=256, given="", wanted=""):
    """One case of ``source`` on the judge host's script; its reply's case."""
    job = {
        "kind": "IO",
        "image": IMAGE,
        "code": _code(source),
        "codeDir": "",
        "compileCmd": "",
        "runCmd": "python3 main.py",
        "timeLimitMs": time_limit_ms,
        "memoryMb": memory_mb,
        "compare": "TRIM_TRAILING",
        "tolerance": 0.0,
        "cases": [{"index": 0, "input": given, "expected": wanted}],
    }
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-I", "-c", RUNNER_SRC],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    took = time.monotonic() - started
    reply = json.loads(done.stdout)
    if not reply.get("ok"):
        raise AssertionError(f"the judging itself failed: {reply}")
    (case,) = reply["cases"]
    case["took_s"] = round(took, 2)
    return case


NETWORK = """
import socket
reached = []
for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53), ("github.com", 443)):
    try:
        socket.create_connection((host, port), timeout=3).close()
        reached.append(host)
    except OSError:
        pass
print("reached " + ",".join(reached) if reached else "blocked")
"""

FORK_BOMB = """
import os, time
started = 0
for _ in range(500):
    try:
        if os.fork() == 0:
            time.sleep(5)
            os._exit(0)
        started += 1
    except OSError:
        break
print(started)
"""

MEMORY = """
hog = []
while True:
    hog.append(bytearray(16 * 1024 * 1024))
"""

SLEEP = """
import time
time.sleep(60)
"""

OUTPUT = """
import sys
chunk = "x" * (1024 * 1024)
for _ in range(20):
    sys.stdout.write(chunk)
"""

WRITES = """
import os
results = []
for path in ("/code/new.txt", "/etc/new.txt", "/new.txt", "/usr/new.txt"):
    try:
        with open(path, "w") as f:
            f.write("x")
        results.append("wrote " + path)
    except OSError:
        pass
with open("/tmp/ok.txt", "w") as f:
    f.write("x")
print(";".join(results) or "denied")
"""

IDENTITY = """
import os
caps = [l for l in open("/proc/self/status") if l.startswith("CapEff")]
print(os.getuid(), caps[0].split()[1])
"""

LOOK_FOR = """
import os, sys
secret = sys.stdin.read().strip()
found = []
SKIP = ("/proc", "/sys", "/dev", "/usr", "/lib", "/lib64", "/bin", "/sbin")
for top in ("/",):
    for root, dirs, files in os.walk(top):
        if root.startswith(SKIP):
            dirs[:] = []
            continue
        for name in files:
            try:
                with open(os.path.join(root, name), "rb") as f:
                    if secret.encode() in f.read(4 * 1024 * 1024):
                        found.append(os.path.join(root, name))
            except OSError:
                pass
for key, value in os.environ.items():
    if secret in value:
        found.append("env " + key)
print("found " + ",".join(found) if found else "nothing")
"""


def check_network():
    case = judge(NETWORK, time_limit_ms=10_000, wanted="blocked")
    assert case["status"] == "ok", f"the container reached the network: {case}"


def check_fork_bomb():
    case = judge(FORK_BOMB, time_limit_ms=5000)
    started = int((case.get("actual") or "0").strip() or 0)
    assert started < 64, f"the process limit let {started} processes start: {case}"


def check_memory():
    case = judge(MEMORY, memory_mb=128, time_limit_ms=5000)
    assert case["status"] == "memory", f"a memory hog was not out of memory: {case}"


def check_sleep():
    case = judge(SLEEP, time_limit_ms=1000)
    assert case["status"] == "timeout", f"a sleeper was not a timeout: {case}"
    assert case["took_s"] < 20, f"the wall clock did not stop the sleeper: {case}"


def check_output():
    case = judge(OUTPUT, time_limit_ms=5000)
    assert case["status"] == "output_limit", f"20 MB of output was not cut: {case}"


def check_read_only():
    case = judge(WRITES, wanted="denied")
    assert case["status"] == "ok", f"the program wrote outside /tmp: {case}"


def check_identity():
    case = judge(IDENTITY)
    uid, caps = (case.get("actual") or "").split()
    assert uid == "1000", f"the program ran as uid {uid}"
    assert int(caps, 16) == 0, f"the program kept capabilities {caps}"


def check_expected_stays_out():
    secret = f"expected-{uuid.uuid4().hex}"
    # The program is told what to look for on stdin; that is the only copy of
    # the secret that goes in, and the expected output carries it too. Seen
    # anywhere else, the expected output reached the container.
    case = judge(LOOK_FOR, time_limit_ms=10_000, given=secret, wanted=secret)
    found = (case.get("actual") or "").strip()
    assert found == "nothing", f"the expected output reached the container: {case}"


CHECKS = [
    check_network,
    check_fork_bomb,
    check_memory,
    check_sleep,
    check_output,
    check_read_only,
    check_identity,
    check_expected_stays_out,
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for check in CHECKS:
        try:
            check()
            results[check.__name__] = "passed"
        except Exception as exc:  # every check reports, the run fails at the end
            results[check.__name__] = f"failed: {exc}"
        print(f"{check.__name__}: {results[check.__name__]}", flush=True)
    (args.output / "results.json").write_text(json.dumps(results, indent=2))
    return 0 if all(v == "passed" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
