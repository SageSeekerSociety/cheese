"""Run commands in Cheese's Linux confinement with the executor's proxy bridge."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REMOTE = (
    HERE.parents[2] / "backend/app/domain/agent/harness/claude_code/remote_execution"
)


def main():
    mode, room, *args = sys.argv[1:]
    home = Path(room).resolve()
    state = home / "state"
    runtime = runpy.run_path(str(REMOTE / "runtime.py"))
    platform = Path(runtime["platform_socket_directory"](state))
    if mode == "prepare":
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"workspace": str(home)}))
        platform.mkdir(mode=0o700)
        print(platform)
        return 0

    if mode == "inside":
        executor = runtime["Executor"](state)
        if executor.forge_bridge is None:
            raise RuntimeError("The executor did not start its proxy bridge")
        if sys.stdin.isatty():
            for name in ("HTTPS_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"):
                print(f"{name}={executor.env[name]}", file=sys.stderr, flush=True)
        return subprocess.run(args, env=executor.env, check=False).returncode

    binary, *command = args
    owner = Path.home().resolve()
    confinement = runpy.run_path(str(REMOTE / "confinement.py"))
    bwrap = os.environ.get("CHEESE_TEST_BWRAP") or shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("Set CHEESE_TEST_BWRAP to the Cheese bubblewrap binary")

    block, unblock = os.pipe()
    seccomp, filter_write = os.pipe()
    info_file = (state / "bubblewrap-info.json").open("wb")
    # Release the command into bubblewrap's loopback-only network namespace.
    os.write(unblock, b"1")
    os.close(unblock)
    os.write(filter_write, confinement["seccomp_filter"]())
    os.close(filter_write)
    fds = {"info": info_file.fileno(), "block": block, "seccomp": seccomp}
    for fd in fds.values():
        os.set_inheritable(fd, True)
    argv = confinement["sandbox_argv"](
        [sys.executable, str(HERE), "inside", str(home), *command],
        bwrap=bwrap,
        owner=owner,
        home=home,
        store=home / "store",
        toolchain=home / "toolchain",
        tmp=home / "tmp",
        releases=REMOTE,
        workdir=home,
        claude=Path(binary),
        sockets=home / "sockets",
        platform=platform,
        fds=fds,
        programs=[HERE.parent],
        network=True,
    )
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "BROWSER": "/usr/bin/false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    os.execve(bwrap, [argv[0], "--die-with-parent", *argv[1:]], env)


if __name__ == "__main__":
    sys.exit(main())
