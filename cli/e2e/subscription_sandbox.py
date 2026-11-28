"""Run subscription sessions using the device launch and Linux executor builders."""

import hashlib
import http.server
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2] / "backend"
REMOTE = BACKEND / "app/domain/agent/harness/claude_code/remote_execution"
METER = HERE.parents[2] / "deploy/metering-proxy/cheese_billing_core.py"
DEVICE = "subscription-e2e"
# The listening socket of the executor route, held by the test across turns
# and handed to each turn as this descriptor.
RELAY_FD = 3


def stage_launch(home, case, ca, project, topic, owner, port):
    sys.path.insert(0, str(BACKEND))
    os.environ["WORKSPACE_ROOT"] = str(home / "platform-data")
    os.environ["SUBSCRIPTION_CA_BACKEND_PATH"] = ca
    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.device_provider import DeviceChannel, screen_launch_env
    from app.domain.agent.harness.claude_code import ClaudeLaunch
    from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
    from app.domain.agent.launch import MachinePlace
    from app.domain.agent.owner_provider import OwnerChannel
    from app.domain.agent.place import OWN_LOGIN_ID, OWN_MODEL
    from app.domain.agent.screen_model_env import screen_model_env
    from app.domain.device.login_kind import FIRST_LOGIN
    from app.domain.project_skill.service import session_skill_bundle

    owner = Path(owner)
    workspace = home / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    owned = case == "owned"
    api_base, handle = "http://127.0.0.1", "subscription-e2e"
    model_env, ca_pem, _ = screen_model_env(
        {"CHEESE_OWN_LOGIN": "1", OWN_LOGIN_ID: FIRST_LOGIN, OWN_MODEL: "sonnet"}
        if owned else {},
        token="",
        project_id=project,
        topic_id=topic,
        resource_id=topic,
        agent_handle=handle,
        api_base=api_base,
        no_proxy=DeviceChannel(public_base=api_base)._no_proxy_hosts(),
    )
    # Where the session runs beside its executor, as the channel that starts
    # it decides: a metered session in a namespace of its own on the session
    # host, a member's own Claude Code as a plain process on its owner's machine.
    channel = OwnerChannel if owned else CentralChannel
    # The target a session holds once its lease names the room's machine
    # (`client._take_leased_machine`), as the channel places it. The executor
    # route it names is on `port`, which the test holds listening from now
    # until its last turn (`RELAY_FD`), so the launch is assembled with the
    # target its session is handed.
    target = {
        "kind": "device", "device_id": DEVICE,
        "url": f"http://127.0.0.1:{port}/executor", "mcp_servers": [],
        **channel._deferred_target(None, {}, apart=False),
    }
    place = MachinePlace(
        home=str(home), workdir=str(workspace), store="", state=str(home / "state"),
        api_base=api_base, project_id=project, topic_id=topic, agent_handle=handle,
        execution_target=target, ca_pem=ca_pem, seat=handle, own_login=owned,
    )
    launch = ClaudeLaunch(system_prompt="").on(place)
    _, env = screen_launch_env(place, launch, model_env, token="", agent_configuration="")

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
    # What the connector starts the launcher with: the machine's own HOME,
    # PATH and owner's home under the screen's environment.
    shell_env = {"HOME": str(home), "PATH": os.environ["PATH"], "REAL_HOME": str(owner), **env}
    raw = subprocess.check_output(["/bin/sh", str(script)], cwd=workspace, env=shell_env)
    (home / "production.json").write_text(
        json.dumps({"env": parse_environ(raw), "args": LAUNCH_ARGS, "target": target})
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


PROXIES = {"HTTPS_PROXY", "https_proxy"}


def stand_ins(built, owned, proxy, ca):
    """What the fixture adds to the environment the builders produced, and
    only that. ``built`` is that environment complete, as the session would be
    started with it: everything checked here holds of it before anything of
    the fixture's is laid over it.

    A metered session's HTTPS_PROXY is its seat's tunnel helper, which the
    platform half of the launch starts and exports when the builders name a
    tunnel (`machine_launcher`: `[ -n "${CHEESE_TUNNEL_URL:-}" ]`). Neither
    runs here: the stub's own port stands in for the helper's, so the builders
    must name a tunnel and no proxy of their own. A member's own session has
    neither proxy nor CA in production; the fixture adds both to reach the
    stub."""
    if owned:
        named = sorted(({"CHEESE_TUNNEL_URL", "NODE_EXTRA_CA_CERTS"} | PROXIES) & set(built))
        if named:
            raise RuntimeError(f"Own-login launch names the metering route: {named}")
        return {"HTTPS_PROXY": proxy, "NODE_EXTRA_CA_CERTS": ca}
    if not built.get("CHEESE_TUNNEL_URL") or PROXIES & set(built):
        raise RuntimeError("Metered launch must name its tunnel and no proxy of its own")
    return {"HTTPS_PROXY": proxy}


def run_session(runtime, home, case, binary, action, proxy, ca=None):
    staged = json.loads((home / "production.json").read_text())
    launched = staged["env"]
    owned = case == "owned"
    if action == "status":
        return subprocess.run(
            [binary, "auth", "status"], env={**launched, **stand_ins(launched, owned, proxy, ca)},
            check=False,
        ).returncode

    state = home / "state"
    with executor(runtime, home, state, binary, launched, network=not owned):
        os.environ.update(launched)
        client = runpy.run_path(str(REMOTE / "client.py"))
        directory = Path((home / "session-dir").read_text().strip())
        launch = client["prepare"](
            directory,
            staged["target"],
            claude=binary,
            extra_args=staged["args"],
            base_settings=json.loads((directory / "base-settings.json").read_text()),
            home_override=home,
            config_override=launched["CLAUDE_CONFIG_DIR"],
            workspace_override=launched["CHEESE_WORK"],
        )
        # From here the launch may hold the project's forwarded view
        # (`client.take_view`), which goes with this run however it ends.
        try:
            production, _ = client["start_gateway"](
                Path(launch["execution"]), {**launched, **launch["env"]}
            )
            fixture = stand_ins(production, owned, proxy, ca)
            env = {**production, **fixture}
            # `namespace.enter` is this Python, which hands the build what it set
            # in its own environment as it started (PEP 538's locale coercion), and
            # starts the session where it sees its project, saying so in PWD.
            entered = (
                {**interpreter_additions(env), "PWD": launch["workspace"]}
                if launch["command"][2:3] == ["enter"]
                else {}
            )
            with subprocess.Popen(launch["command"], env=env, cwd=launch["cwd"]) as process:
                proc = Path(f"/proc/{process.pid}")
                interpreter = os.path.realpath(sys.executable)
                deadline = time.monotonic() + 5
                while os.readlink(proc / "exe") == interpreter or not (raw := (proc / "environ").read_bytes()):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Claude Code did not start in the launch's process")
                    time.sleep(0.005)
                actual = parse_environ(raw)
                audit = {
                    "case": case,
                    "pid": process.pid,
                    "mount_namespace": os.readlink(proc / "ns/mnt"),
                    "network_namespace": os.readlink(proc / "ns/net"),
                    "production": redact(production),
                    "fixture": redact(fixture),
                    "entered": entered,
                    "differs_from_production": redact(
                        {key: value for key, value in actual.items() if production.get(key) != value}
                    ),
                    "missing": sorted(set(production) - set(actual)),
                    "settings_env": redact(json.loads((directory / "settings.json").read_text())["env"]),
                }
                print("SESSION_ENV " + json.dumps(audit), file=sys.stderr, flush=True)
                if actual != {**production, **entered, **fixture}:
                    process.kill()
                    raise RuntimeError("Claude process environment differs from its production launch")
                return process.wait()
        finally:
            # The view outlives the session until its server sees it ended
            # (`release.release_abandoned`); this run's view goes with it.
            runpy.run_path(str(REMOTE / "release.py"))["release_mount"](launch["cwd"])


def interpreter_additions(env):
    """What this Python sets in its own environment when started with `env`."""
    started = json.loads(subprocess.check_output(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"], env=env,
    ))
    return {key: value for key, value in started.items() if env.get(key) != value}


@contextmanager
def executor(runtime, home, state, binary, env, *, network):
    """The room's executor in its sandbox, and the platform's executor route
    to it, served on the socket the test holds at the URL the session's
    target names (`RELAY_FD`)."""
    platform = Path(runtime["platform_socket_directory"](state))
    public = Path(runtime["socket_path"](state))
    ready = Path(runtime["socket_directory"](state)) / "executor.sock"
    command = [sys.executable, str(REMOTE / "runtime.py"), "serve", "--state", str(state)]
    with ExitStack() as stack:
        argv, fds = stack.enter_context(
            sandbox(runtime, home, state, platform, binary, Path(env["REAL_HOME"]), command,
                    network=network)
        )
        service = subprocess.Popen(argv, env=env, pass_fds=fds, stdin=subprocess.DEVNULL)
    relay = None
    try:
        # The connector links the address clients dial to the socket the
        # sandbox binds (`cli/internal/host/executor_unix.go`).
        public.symlink_to(ready)
        # A socket file left by an earlier turn's executor is there before
        # this one listens: ready is when it answers.
        until = time.monotonic() + 5
        while True:
            try:
                runtime["request"](state, "ping")
                break
            except OSError:
                if service.poll() is not None or time.monotonic() >= until:
                    raise RuntimeError("Executor did not answer") from None
                time.sleep(0.01)
        relay = executor_route(runtime, state, socket.socket(fileno=RELAY_FD))
        yield
    finally:
        if relay is not None:
            relay.shutdown()
            relay.server_close()
        if service.poll() is None:
            service.terminate()
        service.wait(timeout=5)
        public.unlink(missing_ok=True)


def executor_route(runtime, state, listener):
    """The platform's route to a device's executor, standing in for the
    platform and the connector: each call goes to the executor at `state`."""

    class Route(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            call = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                status, answer = 200, runtime["request"](state, call["method"], call.get("params"))
            except RuntimeError as error:
                status, answer = 500, {"detail": str(error)}
            body = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(
        listener.getsockname(), Route, bind_and_activate=False
    )
    server.socket.close()
    server.socket = listener
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@contextmanager
def sandbox(runtime, home, state, platform, binary, owner, command, *, network):
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
    try:
        argv = confinement["sandbox_argv"](
            command,
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
            network=network,
        )
        yield [argv[0], "--die-with-parent", *argv[1:]], tuple(fds.values())
    finally:
        os.close(block)
        os.close(seccomp)
        info_file.close()


def meter_config(case, project, topic):
    core = runpy.run_path(str(METER))
    config = {"placeholder": core["NO_LOGIN_PLACEHOLDER"], "project": project, "topic": topic}
    if case == "metered-no-credential":
        config["noLoginAnswers"] = {
            path: body.decode() for path, body in core["NO_LOGIN_ANSWERS"].items()
        }
    print(json.dumps(config))


def audit_meter(config):
    config = json.loads(config)
    core = runpy.run_path(str(METER))
    failures = []
    for line in json.load(sys.stdin):
        request, _, result = line.partition(" -> ")
        host, _, path = request.split(" ", 2)
        status, _, details = result.partition(" (")
        decision, _, caller = details.removesuffix(")").partition(", ")
        table = core["control_answer"](host, path, config["project"], config["topic"])
        if table is not None:
            expected, code = "table", table.status
        elif "noLoginAnswers" in config and caller == "placeholder":
            expected, code = (
                ("no-login", 200)
                if core["no_login_answer"](host, path) is not None
                else ("no-credential", 400)
            )
        else:
            expected, code = "upstream", None
        if decision != expected or (code is not None and int(status) != code):
            failures.append(f"{line}; expected {expected}" + (f" {code}" if code is not None else ""))
    if failures:
        sys.exit("Meter routing differs from production:\n" + "\n".join(failures))


def main():
    mode, *parameters = sys.argv[1:]
    if mode == "meter":
        meter_config(*parameters)
        return 0
    if mode == "audit-meter":
        audit_meter(*parameters)
        return 0
    room, *args = parameters
    home = Path(room).resolve()
    if mode == "stage":
        stage_launch(home, *args)
        return 0
    state = home / "state"
    runtime = runpy.run_path(str(REMOTE / "runtime.py"))
    if mode == "prepare":
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"workspace": str(home / "workspace")}))
        Path(runtime["platform_socket_directory"](state)).mkdir(mode=0o700)
        print(json.dumps({
            "platform": runtime["platform_socket_directory"](state),
            "sockets": runtime["socket_directory"](state),
        }))
        return 0
    return run_session(runtime, home, *args)


if __name__ == "__main__":
    sys.exit(main())
