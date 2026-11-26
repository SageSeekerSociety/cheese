"""Run subscription sessions using the device launch and Linux executor builders."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2] / "backend"
REMOTE = BACKEND / "app/domain/agent/harness/claude_code/remote_execution"


def stage_launch(home, case, ca, project, topic):
    sys.path.insert(0, str(BACKEND))
    os.environ["WORKSPACE_ROOT"] = str(home / "platform-data")
    os.environ["SUBSCRIPTION_CA_BACKEND_PATH"] = ca
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
    from app.domain.agent.harness.claude_code.device_launch import launch_holes
    from app.domain.agent.launch import MachinePlace
    from app.domain.agent.machine_launcher import screen_env
    from app.domain.agent.place import OWN_LOGIN_ID, OWN_MODEL, seat_name
    from app.domain.agent.screen_model_env import screen_model_env
    from app.domain.device.login_kind import FIRST_LOGIN
    from app.domain.project_skill.service import session_skill_bundle

    owner = home / "owner"
    workspace = home / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    owned = case == "owned"
    place = MachinePlace(
        home=str(home), workdir=str(workspace), store="", state=str(home / "state"),
        api_base="http://127.0.0.1", project_id=project, topic_id=topic,
        agent_handle="subscription-e2e", own_login=owned,
    )
    model_env, ca_pem, _ = screen_model_env(
        {"CHEESE_OWN_LOGIN": "1", OWN_LOGIN_ID: FIRST_LOGIN, OWN_MODEL: "sonnet"}
        if owned else {},
        token="",
        project_id=project,
        topic_id=topic,
        resource_id=topic,
        agent_handle=place.agent_handle,
        api_base=place.api_base,
        no_proxy=DeviceChannel(public_base=place.api_base)._no_proxy_hosts(),
    )
    launch = launch_holes(
        state=place.state, ca_pem=ca_pem, own_login=owned,
        seat=seat_name(place.agent_handle),
    )

    bundle = session_skill_bundle(None)
    cache = owner / ".cheese/skill-bundles"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / (hashlib.sha256(bundle).hexdigest() + ".json.gz")).write_bytes(bundle)
    script = home / "configure.sh"
    script.write_text(
        "set -e\ncheese_launch_phase() { :; }\n"
        + launch.configure
        + launch.credentials
        + 'printf "%s\\n" "$SEAT/remote-session" > "$HOME/session-dir"\n'
        + "exec env -0\n"
    )
    shell_env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "REAL_HOME": str(owner),
        **screen_env(place, token=""),
        **model_env,
        **launch.env,
    }
    raw = subprocess.check_output(["/bin/sh", str(script)], cwd=workspace, env=shell_env)
    (home / "production.json").write_text(
        json.dumps({"env": parse_environ(raw), "args": LAUNCH_ARGS})
    )


def parse_environ(raw):
    return dict(entry.split("=", 1) for entry in raw.decode().split("\0") if entry)


def redact(env):
    return {
        key: "***" + value[-4:]
        if value and any(part in key for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
        and key != "CHEESE_TOKEN_EXPIRES"
        else value
        for key, value in sorted(env.items())
    }


def run_session(runtime, home, state, platform, case, binary, action):
    case_env = json.loads((home / "production.json").read_text())
    production = case_env["env"]
    bridge = runtime["_start_forge_bridge"](platform / "forge.sock")
    proxy = f"http://127.0.0.1:{bridge.server_address[1]}"
    additions = {"HTTPS_PROXY": proxy}
    if case == "owned":
        additions["NODE_EXTRA_CA_CERTS"] = str(platform / "ca.pem")
    else:
        # The meter tunnel publishes its selected loopback port to the launch.
        production["HTTPS_PROXY"] = proxy

    if action == "status":
        return subprocess.run(
            [binary, "auth", "status"], env={**production, **additions}, check=False
        ).returncode

    service = subprocess.Popen(
        [sys.executable, str(REMOTE / "runtime.py"), "serve", "--state", str(state)],
        env=production,
        stdin=subprocess.DEVNULL,
    )
    try:
        until = time.monotonic() + 5
        while not Path(runtime["socket_path"](state)).exists():
            if service.poll() is not None or time.monotonic() >= until:
                raise RuntimeError("Executor did not open its socket")
            time.sleep(0.01)
        os.environ.update(production)
        client = runpy.run_path(str(REMOTE / "client.py"))
        directory = Path((home / "session-dir").read_text().strip())
        launch = client["prepare"](
            directory,
            {
                "kind": "local",
                "local": True,
                "workspace": production["CHEESE_WORK"],
                "context_tree": {"generation": "fixture", "entries": {}},
                "state": str(state),
                "command": [sys.executable, str(REMOTE / "runtime.py")],
            },
            claude=binary,
            extra_args=case_env["args"],
            base_settings=json.loads((directory / "base-settings.json").read_text()),
            home_override=home,
            config_override=production["CLAUDE_CONFIG_DIR"],
        )
        production.update(launch["env"])
        expected, _ = client["start_gateway"](Path(production[client["EXECUTION_CONFIG"]]), production)
        env = {**expected, **additions}
        with subprocess.Popen(launch["command"], env=env, cwd=launch["cwd"]) as process:
            environ = Path(f"/proc/{process.pid}/environ")
            deadline = time.monotonic() + 1
            while not (raw := environ.read_bytes()):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Claude's /proc environment was empty")
                time.sleep(0.001)
            actual = parse_environ(raw)
            missing = sorted(set(expected) - set(actual))
            added = {key: value for key, value in actual.items() if key not in expected}
            changed = {
                key: value for key, value in actual.items()
                if key in expected and value != expected[key]
            }
            audit = {
                "case": case, "pid": process.pid,
                "production": redact(expected), "actual": redact(actual),
                "added": redact(added), "changed": redact(changed), "missing": missing,
                "settings_env": json.loads((directory / "settings.json").read_text())["env"],
            }
            print("SESSION_ENV " + json.dumps(audit), file=sys.stderr, flush=True)
            if actual != env:
                process.kill()
                raise RuntimeError("Claude process environment differs from its launch")
            return process.wait()
    finally:
        if service.poll() is None:
            service.terminate()
        service.wait(timeout=5)


def main():
    mode, *parameters = sys.argv[1:]
    if mode == "placeholder":
        sys.path.insert(0, str(BACKEND))
        from app.domain.agent.harness.claude_code.device_launch import NO_LOGIN_PLACEHOLDER
        print(NO_LOGIN_PLACEHOLDER)
        return 0
    room, *args = parameters
    home = Path(room).resolve()
    if mode == "stage":
        stage_launch(home, *args)
        return 0
    state = home / "state"
    runtime = runpy.run_path(str(REMOTE / "runtime.py"))
    platform = Path(runtime["platform_socket_directory"](state))
    if mode == "prepare":
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"workspace": str(home / "workspace")}))
        platform.mkdir(mode=0o700)
        print(json.dumps({"platform": str(platform), "sockets": str(runtime["socket_directory"](state))}))
        return 0

    if mode == "inside":
        return run_session(runtime, home, state, platform, *args)

    case, binary, action = args
    owner = Path.home().resolve()
    confinement = runpy.run_path(str(REMOTE / "confinement.py"))
    bwrap = os.environ.get("CHEESE_TEST_BWRAP") or shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("Set CHEESE_TEST_BWRAP to the Cheese bubblewrap binary")

    block, unblock = os.pipe()
    seccomp, filter_write = os.pipe()
    info_file = (state / "bubblewrap-info.json").open("wb")
    os.write(unblock, b"1")
    os.close(unblock)
    os.write(filter_write, confinement["seccomp_filter"]())
    os.close(filter_write)
    fds = {"info": info_file.fileno(), "block": block, "seccomp": seccomp}
    for fd in fds.values():
        os.set_inheritable(fd, True)
    argv = confinement["sandbox_argv"](
        [sys.executable, str(HERE), "inside", str(home), case, binary, action],
        bwrap=bwrap,
        owner=owner,
        home=home,
        store=home / "store",
        toolchain=home / "toolchain",
        tmp=home / "tmp",
        releases=REMOTE.parents[2],
        workdir=home,
        claude=Path(binary),
        sockets=Path(runtime["socket_directory"](state)),
        platform=platform,
        fds=fds,
        programs=[HERE.parent, BACKEND / "sandbox"],
        network=True,
    )
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    os.execve(bwrap, [argv[0], "--die-with-parent", *argv[1:]], env)


if __name__ == "__main__":
    sys.exit(main())
