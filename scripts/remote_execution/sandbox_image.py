#!/usr/bin/env python3
"""Measure executor readiness and rootfs quota behavior on a disposable VM.

Run after enrollment's data-disk and tool preparation, as its ordinary login:
  python3 sandbox_image.py --release RELEASE --baseline-release BASELINE \
      --claude PINNED_BINARY --image GHCR_DIGEST --output RESULTS
The script starts only its own sandboxes and tears them down in finally blocks.
It leaves receipts in RESULTS; provisioning and deleting the VM are separate.
"""

import argparse
import base64
import contextlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def disk_bytes(path):
    return int(
        subprocess.check_output(
            ["sudo", "-n", "du", "-s", "-B1", str(path)], text=True
        ).split()[0]
    )


def disk_inodes(path):
    return int(
        subprocess.check_output(
            ["sudo", "-n", "du", "--inodes", "-s", str(path)], text=True
        ).split()[0]
    )


class Sandbox:
    def __init__(self, release, claude, image, output, disk_mib=64):
        self.release, self.claude, self.image = release, claude, image
        self.name = str(uuid.uuid4())
        self.owner = Path.home()
        self.home = self.owner / ".cheese/home" / str(uuid.uuid4()) / self.name
        self.work = self.home / "room"
        self.state = self.home / ".cheese/executor"
        self.work.mkdir(parents=True)
        self.state.mkdir(parents=True)
        self.bootstrap = load(
            release / "remote-execution/bootstrap.py", "bench_bootstrap"
        )
        self.runtime = load(release / "remote-execution/runtime.py", "bench_runtime")
        self.limits = dict(
            memory_mb=3072,
            swap_mb=0,
            cpus=2,
            pids=4096,
            disk_mib=disk_mib,
            inodes=100000,
        )
        self.env = {
            name: os.environ[name]
            for name in ("PATH", "LANG", "USER", "SHELL")
            if name in os.environ
        }
        self.env["HOME"] = str(self.home)
        self.env["CHEESE_STORE"] = str(
            self.owner / ".cheese/store" / self.home.parent.name
        )
        self.env["CHEESE_TOOLCHAIN"] = str(self.owner / ".cheese/toolchain")
        bins = [
            str(path) for path in (self.owner / ".cheese/toolchain/node").glob("*/bin")
        ]
        self.env["PATH"] = ":".join(
            [
                *bins,
                str(self.owner / ".cheese/toolchain/bin"),
                self.env.get("PATH", "/usr/bin:/bin"),
            ]
        )
        if image:
            self.env.update(
                USER="root",
                LOGNAME="root",
                IS_SANDBOX="1",
                CHEESE_CONNECTOR_UID=str(os.getuid()),
                PATH=self.env["PATH"] + ":/usr/local/sbin:/usr/sbin:/sbin",
            )
        config = {
            "workspace": str(self.work),
            "claude": str(claude),
            "env": self.env,
            "release": str(release),
            "sandbox": self.limits,
            "sandbox_image": self.image,
        }
        (self.state / "config.json").write_text(json.dumps(config))
        self.log = (output / (self.name + ".log")).open("w")
        self.process = None
        address = Path(self.runtime.socket_path(self.state))
        address.symlink_to(
            Path(self.runtime.socket_directory(self.state)) / "executor.sock"
        )

    def start(self, snapshot=None):
        began = time.monotonic()
        args = dict(
            bwrap=str(self.owner / ".cheese/runtime/bubblewrap/bwrap"),
            owner=self.owner,
            home=self.home,
            claude=self.claude,
            sockets=Path(self.runtime.socket_directory(self.state)),
            limits=self.limits,
            env=self.env,
            connect=True,
            release=self.release,
            cwd=self.work,
            stdout=self.log,
            stderr=self.log,
            stdin=subprocess.DEVNULL,
        )
        if self.image:
            args["image"] = self.image
        if snapshot is not None:
            args["root_snapshot"] = snapshot
        self.process, self.started = self.bootstrap.start_sandbox(
            [
                sys.executable,
                str(self.release / "remote-execution/runtime.py"),
                "serve",
                "--state",
                str(self.state),
            ],
            **args,
        )
        deadline = time.monotonic() + 15
        while True:
            try:
                self.runtime.request(self.state, "ping")
                return round(time.monotonic() - began, 4)
            except (ConnectionError, FileNotFoundError):
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        "executor did not become ready; inspect " + self.log.name
                    ) from None
                time.sleep(0.01)

    def command(self, body):
        ident = "image-eval-" + uuid.uuid4().hex
        common = {"subtype": "shell", "command_id": ident}
        self.runtime.request(
            self.state,
            "control",
            {
                **common,
                "operation": "start",
                "kind": "sh",
                "body": body,
                "cwd": str(self.work),
                "env": {},
                "merge": True,
            },
        )
        out, err, deadline = b"", b"", time.monotonic() + 600
        while True:
            answer = self.runtime.request(
                self.state,
                "control",
                {
                    **common,
                    "operation": "read",
                    "out": len(out),
                    "err": len(err),
                    "wait": 1,
                },
            )
            out += base64.b64decode(answer["out"])
            err += base64.b64decode(answer["err"])
            if "exit" in answer:
                return {
                    "exit": answer["exit"],
                    "stdout": out.decode(),
                    "stderr": err.decode(),
                }
            if time.monotonic() >= deadline:
                raise TimeoutError(body)

    def stop(self):
        if self.process is not None:
            subprocess.run(
                [
                    "sudo",
                    "-n",
                    "/usr/local/libexec/cheese-sandbox",
                    "down",
                    self.name,
                ],
                check=True,
                capture_output=True,
            )
            self.process.wait(timeout=15)
            self.process = None

    def close(self):
        try:
            self.stop()
        finally:
            self.log.close()
            if self.home.exists():
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "/usr/local/libexec/cheese-sandbox",
                        "remove-home",
                        self.name,
                        str(self.home),
                    ],
                    check=True,
                    capture_output=True,
                )
                assert not self.home.exists()


@contextlib.contextmanager
def started(*args):
    sandbox = Sandbox(*args)
    try:
        ready = sandbox.start()
        yield sandbox, ready
    finally:
        sandbox.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "baseline-release", "claude", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--packages", action="store_true")
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.error("run as the connector's ordinary user")
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = {"kernel": os.uname().release, "image": args.image, "quota_mib": 64}
    if args.cold:
        began = time.monotonic()
        prepared = subprocess.run(
            [
                "sudo",
                "-n",
                sys.executable,
                "-I",
                str(args.release / "remote-execution/sandbox_image.py"),
                args.image,
                "--host-helper",
                (args.release / "remote-execution/sandbox_host.py").read_text(),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        receipt["cold_prepare"] = json.loads(prepared.stdout)
        with started(args.release, args.claude, args.image, args.output) as (_, ready):
            receipt["cold_host_ready_s"] = round(time.monotonic() - began, 4)
            receipt["cold_executor_ready_s"] = ready
    warm = subprocess.run(
        [
            "sudo",
            "-n",
            sys.executable,
            "-I",
            str(args.release / "remote-execution/sandbox_image.py"),
            args.image,
            "--host-helper",
            (args.release / "remote-execution/sandbox_host.py").read_text(),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    receipt["warm_prepare"] = json.loads(warm.stdout)
    receipt["rootfs_bytes"] = disk_bytes(
        Path("/data/cheese-sandbox-images")
        / ("sha256-" + args.image.split("sha256:")[1])
        / "rootfs"
    )
    for name, release, image in (
        ("host_root", args.baseline_release, None),
        ("image_root", args.release, args.image),
    ):
        samples = []
        for _ in range(args.samples):
            with started(release, args.claude, image, args.output) as (sandbox, ready):
                samples.append(ready)
                if name == "host_root" and not receipt.get("baseline_compiler"):
                    receipt["baseline_compiler"] = sandbox.command(
                        "command -v gcc; command -v make"
                    )
        receipt[name] = {
            "ready_s": samples,
            "mean_s": round(statistics.mean(samples), 4),
        }
        (args.output / "result.json").write_text(json.dumps(receipt, indent=2))
    with (
        started(args.release, args.claude, args.image, args.output) as (one, _),
        started(args.release, args.claude, args.image, args.output) as (two, _),
    ):
        layer = one.home / ".cheese/rootfs"
        receipt["idle_layer_bytes"] = disk_bytes(layer)
        receipt["idle_layer_inodes"] = disk_inodes(layer)
        checks = {
            "compile": "printf '#include <stdio.h>\\nint main(void){puts(\"c works\");return 0;}\\n' > main.c; printf 'all:\\n\\tgcc -g main.c -o main\\n' > Makefile; make && ./main && g++ main.c -o main-cpp && ./main-cpp",
            "claude": str(args.claude) + " --version",
            "uid_map": "cat /proc/self/uid_map; cat /proc/self/gid_map",
            "identity": 'id -un && getent passwd "$(id -u)"',
            "writable_root": "touch /usr/local/bin/cheese-eval && mkdir /opt/cheese-eval && echo cheese-eval >> /etc/debian_version && tail -1 /etc/debian_version",
            "gdb": "gdb -q -batch -ex 'break main' -ex run -ex 'print 42' -ex continue --args ./main",
            "strace": "strace -f -e trace=execve /bin/true",
            "git": "git init && git status --porcelain && stat -c '%u:%g' .git/config",
            "network": "getent ahostsv4 github.com && curl -fsS --max-time 20 https://github.com/ -o /dev/null",
        }
        receipt["checks"] = {name: one.command(body) for name, body in checks.items()}
        with subprocess.Popen(["sleep", "60"]) as outside:
            try:
                receipt["outside_ptrace"] = one.command(
                    f"test ! -e /proc/{outside.pid} && strace -p {outside.pid}"
                )
                assert receipt["outside_ptrace"]["exit"] != 0
                assert "No such process" in receipt["outside_ptrace"]["stdout"]
                assert outside.poll() is None
            finally:
                outside.terminate()
        assert receipt["checks"]["compile"]["exit"] == 0, receipt["checks"]["compile"]
        assert receipt["checks"]["claude"]["exit"] == 0, receipt["checks"]["claude"]
        assert receipt["checks"]["identity"]["exit"] == 0, receipt["checks"]["identity"]
        for name in ("writable_root", "gdb", "strace", "git"):
            assert receipt["checks"][name]["exit"] == 0, receipt["checks"][name]
        receipt["home_write"] = one.command(
            "dd if=/dev/zero of=home-fill bs=1M count=32 status=none"
        )
        receipt["layer_fill"] = one.command(
            "dd if=/dev/zero of=/layer-fill bs=1M count=96 status=none"
        )
        assert receipt["layer_fill"]["exit"] != 0, "rootfs escaped the sandbox quota"
        assert any(
            word in receipt["layer_fill"]["stdout"]
            for word in ("No space left", "Disk quota exceeded")
        ), receipt["layer_fill"]
        receipt["full_layer_bytes"] = disk_bytes(layer)
        receipt["neighbor"] = two.command(
            "test ! -e /layer-fill && dd if=/dev/zero of=neighbor bs=1M count=1 status=none && echo neighbor-ok"
        )
        assert receipt["neighbor"]["exit"] == 0, receipt["neighbor"]
        receipt["full_ping"] = bool(one.runtime.request(one.state, "ping"))
    (args.output / "result.json").write_text(json.dumps(receipt, indent=2))
    if args.packages:

        def install(package):
            with started(args.release, args.claude, args.image, args.output, 4096) as (
                sandbox,
                _,
            ):
                outcome = sandbox.command(
                    "apt-get update && apt-get install -y "
                    + package
                    + " && dpkg-query -W "
                    + package
                )
                outcome["installed"] = sandbox.command(
                    "dpkg-query -W -f='${Status} ${Version}\\n' " + package
                )
                outcome["layer_bytes"] = disk_bytes(sandbox.home / ".cheese/rootfs")
                if outcome["exit"] == 0 and package == "nginx":
                    outcome["service"] = sandbox.command(
                        "nginx && curl -fsS http://127.0.0.1/ -o /tmp/nginx-response "
                        '&& test "$(ps -C nginx -o uid= | sort -u | tail -1 | xargs)" = "$(id -u www-data)" '
                        "&& ps -eo uid,gid,args | grep '[n]ginx: worker' "
                        "&& wc -c /tmp/nginx-response; code=$?; nginx -s stop; exit $code"
                    )
                if outcome["exit"] == 0 and package == "postgresql":
                    outcome["service"] = sandbox.command(
                        "pg_ctlcluster 17 main start && runuser -u postgres -- "
                        "psql -d postgres -Atqc 'select current_user, 42;' "
                        '&& test "$(ps -C postgres -o uid= | sort -u | xargs)" = "$(id -u postgres)" '
                        "&& ps -eo uid,gid,args | grep '[p]ostgres: 17/main'; "
                        "code=$?; pg_ctlcluster 17 main stop; exit $code"
                    )
                (args.output / (package + ".json")).write_text(
                    json.dumps(outcome, indent=2)
                )
                if "service" in outcome:
                    assert outcome["service"]["exit"] == 0, outcome["service"]
                print(
                    json.dumps(
                        {
                            "package": package,
                            "exit": outcome["exit"],
                            "layer_bytes": outcome["layer_bytes"],
                        }
                    ),
                    flush=True,
                )
                return package, outcome

        packages = (
            "jq",
            "ffmpeg",
            "libpq-dev",
            "postgresql-client",
            "python3-dev",
            "graphviz",
            "ripgrep",
            "unzip",
            "nginx",
            "postgresql",
        )
        with ThreadPoolExecutor(max_workers=2) as workers:
            receipt["packages"] = dict(workers.map(install, packages))
        with started(args.release, args.claude, args.image, args.output, 4096) as (
            sandbox,
            _,
        ):
            receipt["root_compatibility"] = {
                "python": sandbox.command(
                    "uv venv --seed .venv && .venv/bin/python -m pip install --no-index --no-deps pip && .venv/bin/python -m pip --version"
                ),
                "npm": sandbox.command(
                    "npm init -y && npm install --package-lock-only --ignore-scripts"
                ),
                "store_ownership": sandbox.command(
                    'touch "$HOME/home-file" "$CHEESE_STORE/store-file"; stat -c \'%u:%g\' "$HOME/home-file" "$CHEESE_STORE/store-file"'
                ),
                "useradd": sandbox.command(
                    "useradd --create-home cheese-eval-user && "
                    "runuser -u cheese-eval-user -- sh -c "
                    "'id; umask 077; mkdir /home/cheese-eval-user/private; "
                    "echo user-owned > /home/cheese-eval-user/private/file'"
                ),
                "claude_startup": sandbox.command(
                    "ANTHROPIC_BASE_URL=http://127.0.0.1:9 ANTHROPIC_API_KEY=cheese-eval-placeholder timeout 20 "
                    + str(args.claude)
                    + " --dangerously-skip-permissions -p test --max-turns 0"
                ),
            }
            receipt["host_file_ownership"] = {
                "home": sandbox.home.joinpath("home-file").stat().st_uid,
                "store": Path(sandbox.env["CHEESE_STORE"])
                .joinpath("store-file")
                .stat()
                .st_uid,
                "connector": os.getuid(),
            }
            assert receipt["root_compatibility"]["useradd"]["exit"] == 0
            private = (
                sandbox.home
                / ".cheese/rootfs"
                / ("sha256-" + args.image.split("sha256:")[1])
                / "upper/home/cheese-eval-user/private/file"
            )
            receipt["subordinate_file_owner"] = int(
                subprocess.check_output(
                    ["sudo", "-n", "stat", "-c", "%u", str(private)], text=True
                )
            )
            assert receipt["subordinate_file_owner"] != os.getuid()
    (args.output / "result.json").write_text(json.dumps(receipt, indent=2))
    if args.packages:
        assert all(result["exit"] == 0 for result in receipt["packages"].values())
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
