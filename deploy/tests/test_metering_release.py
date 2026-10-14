"""Exercise the metering proxy's two-slot release against a fake docker, compose and front."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release-metering-proxy.sh"
DIGEST = "ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:" + "b" * 64
LEGACY = "cheese-metering-proxy"

# A docker CLI whose containers, front servers and open connections live in a
# JSON file. Containers are named by their compose service. Every call is
# appended to CALLS.
FAKE = r"""#!/usr/bin/env python3
import fcntl, json, os, pathlib, sys
args = sys.argv[1:]
LEGACY = "cheese-metering-proxy"
state_path = pathlib.Path(os.environ["STATE"])
stdin = sys.stdin.read() if "exec" in args and "-i" in args else ""
lock = open(str(state_path) + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
state = json.loads(state_path.read_text())
record = {
    "args": args,
    "image": os.environ.get("METERING_PROXY_IMAGE"),
    "config": os.environ.get("METERING_PROXY_CONFIG_SHA256"),
    "stdin": stdin,
}
if args[0] == "compose":
    files = [args[i + 1] for i, a in enumerate(args) if a == "-f" and args[i + 1].endswith(".yml")]
    record["configs"] = [pathlib.Path(f).read_text() for f in files]
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps(record) + "\n")
out, code = "", 0
running = state["running"]

def take(queue_name, key):
    queue = state[queue_name].get(key, [0])
    value = queue.pop(0) if len(queue) > 1 else queue[0]
    state[queue_name][key] = queue
    return value

def proc_tcp(container):
    lines = ["  sl  local_address rem_address   st"]
    for port in (8444, 8445):
        for _ in range(take("open", f"{container}:{port}")):
            lines.append(f"   0: 0200A8C0:{port:04X} 0100A8C0:C350 01 00000000:00000000")
    return "\n".join(lines)

if args[0] == "pull":
    done = state["pulls"]
    state["pulls"] += 1
    code = 1 if done < int(os.environ.get("FAIL_PULLS", "0")) else 0
elif args[:2] == ["image", "inspect"]:
    out = os.environ.get("FAKE_DIGEST", "ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:" + "b" * 64)
elif args[:2] == ["network", "inspect"] and os.environ.get("NO_NETWORK"):
    code = 1
elif args[0] == "compose":
    sub = next(a for a in args if a in ("ps", "up", "stop", "rm", "config"))
    service = args[-1]
    if sub == "ps":
        out = service if service in running else ""
    elif sub == "config":
        out = ("front " + os.environ.get("WANTED_FRONT_HASH", "hash")) if "--hash" in args else "haproxy:pinned"
    elif sub == "up":
        if os.environ.get("FAIL_UP") == service:
            code = 1
        elif "--no-start" not in args:
            if service == "front" and state.get("legacy_running"):
                code = 1  # the address is still held
            else:
                running.append(service)
                state["started"].append(service)
    elif sub in ("stop", "rm"):
        if service in running:
            running.remove(service)
elif args[0] == "inspect":
    name = args[1]
    fmt = args[-1]
    if name == LEGACY and len(args) == 2:
        code = 0 if state.get("legacy") else 1
    elif "project.working_dir" in fmt:
        out = os.environ["METERING_PROXY_HOME"]
    elif "compose.project" in fmt:
        out = os.environ.get("FAKE_PROJECT", "metering-proxy")
    elif "NetworkSettings" in fmt:
        out = "192.0.2.5"
    elif "config-hash" in fmt:
        out = os.environ.get("RUNNING_FRONT_HASH", "hash")
    elif "config-sha256" in fmt:
        out = os.environ.get("FAKE_CONFIG", "")
    elif ".Config.Image" in fmt:
        out = os.environ.get("FAKE_CURRENT_IMAGE", "ghcr.io/old@sha256:" + "a" * 64)
    else:
        out = "sha256:previous"
elif args[0] == "stop" and args[-1] == LEGACY:
    state["legacy_running"] = False
elif args[0] == "start" and args[-1] == LEGACY:
    state["legacy_running"] = True
elif args[0] == "rm" and args[-1] == LEGACY:
    state["legacy"] = False
elif args[0] == "exec" and "socat" in args:
    container = args[2]
    for command in stdin.strip().split(";"):
        words = command.split()
        if words[:2] == ["set", "server"]:
            server = words[2].split("/")[1]
            state["admin"].setdefault(container, {})[server] = 8 if words[-1] == "drain" else 0
        elif words[:3] == ["show", "servers", "state"]:
            out += "1\n# be_id be_name srv_id srv_name srv_addr srv_op_state srv_admin_state\n"
            for color in ("blue", "green"):
                op = 2 if "slot-" + color in running else 0
                admin = state["admin"].get(container, {}).get("slot-" + color, 0)
                if os.environ.get("SWITCH_BACK") == color and admin == 8 and state["drain_polls"] > 1:
                    admin = 0
                out += f"3 connect_slots 1 slot-{color} 10.0.0.1 {op} {admin}\n"
        elif words[:2] == ["show", "stat"]:
            state["stot"] += 1
            out += "# pxname,svname,qcur,qmax,scur,smax,slim,stot\n"
            out += f"connect,FRONTEND,0,0,0,0,0,{state['stot']}\n"
            out += f"connect_slots,slot-blue,0,0,{take('front_open', container)},0,0,0\n"
elif args[0] == "exec" and "cat" in args and "/proc/net/tcp" in args:
    container = args[1]
    if container.startswith("slot-"):
        state["drain_polls"] += 1
    out = proc_tcp(container)
state_path.write_text(json.dumps(state))
if out:
    print(out)
sys.exit(code)
"""

# The host's own tools the release calls: iptables through sudo, and python3
# for the probe through the steered address.
SUDO = '#!/bin/sh\necho "$*" >> "$SUDO_CALLS"\n'
PYTHON = '#!/bin/sh\necho "$*" >> "$PYTHON_CALLS"\nexit "${PROBE_STATUS:-0}"\n'

ENV_TEXT = "TEST_SECRET=private-test-value\n"


class MeteringReleaseTest(unittest.TestCase):
    def release(self, *, running=("front", "slot-blue"), legacy=False, open=None,
                front_open=None, env_text=ENV_TEXT, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".env").write_text(env_text)
            # The fake runs on this interpreter: `python3` on its PATH is the shim.
            fake = FAKE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1)
            for name, text in (("docker", fake), ("sudo", SUDO), ("python3", PYTHON)):
                (home / name).write_text(text)
                (home / name).chmod(0o755)
            state = home / "state.json"
            state.write_text(json.dumps({
                "running": list(running),
                "started": [],
                "legacy": legacy,
                "legacy_running": legacy,
                "admin": {"front": {"slot-blue": 0}} if "slot-blue" in running else {},
                "open": open or {},
                "front_open": front_open or {},
                "pulls": 0,
                "stot": 0,
                "drain_polls": 0,
            }))
            env = dict(
                os.environ,
                PATH=f"{home}:{os.environ['PATH']}",
                METERING_PROXY_HOME=folder,
                GITHUB_ACTIONS="true",
                GITHUB_RUN_ID="123",
                GITHUB_RUN_ATTEMPT="1",
                STATE=str(state),
                CALLS=str(home / "calls"),
                SUDO_CALLS=str(home / "sudo.calls"),
                PYTHON_CALLS=str(home / "python.calls"),
                METERING_POLL_SECONDS="0",
                METERING_DRAIN_SECONDS="3",
                METERING_PULL_RETRY_DELAYS="0 0 0",
                METERING_PROXY_IMAGE="stale-runner-image",
            )
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(SCRIPT), "a" * 40], env=env, capture_output=True, text=True
            )
            read = lambda name: (home / name).read_text().splitlines() if (home / name).exists() else []
            calls = [json.loads(line) for line in read("calls")]
            self.sudo = read("sudo.calls")
            self.state = json.loads(state.read_text())
            self.installed_login = all(
                (home / name).is_file() and os.access(home / name, os.X_OK)
                for name in ("claude-login.sh", "chatgpt-login.sh")
            )
            # Nothing the release prints, keeps or labels a container with
            # holds a secret from the .env, and the .env is left as it was.
            self.assertNotIn("private-test-value", result.stdout + result.stderr)
            for kept in (home / "releases").rglob("*"):
                if kept.is_file():
                    self.assertNotIn("private-test-value", kept.read_text(), kept)
            for call in calls:
                self.assertNotIn("private-test-value", call["config"] or "")
            self.assertEqual((home / ".env").read_text(), env_text)
            return result, calls

    @staticmethod
    def runtime(calls):
        return [c["stdin"].strip() for c in calls if "socat" in c["args"] and c["stdin"].startswith("set")]

    @staticmethod
    def index(calls, predicate):
        return next(i for i, c in enumerate(calls) if predicate(c["args"]))

    def test_a_release_moves_new_connections_then_stops_the_old_slot_once_drained(self):
        result, calls = self.release(open={"slot-blue:8444": [3, 1, 0]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0]["args"], ["pull", "ghcr.io/sageseekersociety/cheese/metering-proxy:" + "a" * 40])
        up = self.index(calls, lambda a: a[0] == "compose" and "up" in a and a[-1] == "slot-green")
        self.assertEqual(calls[up]["image"], DIGEST)
        self.assertIn("--force-recreate", calls[up]["args"])
        # Held back until healthy, then switched: green ready and blue drained
        # in the same moves, on both listeners.
        self.assertEqual(self.runtime(calls), [
            "set server connect_slots/slot-green state drain; set server chatgpt_slots/slot-green state drain",
            "set server connect_slots/slot-green state ready; set server chatgpt_slots/slot-green state ready",
            "set server connect_slots/slot-blue state drain; set server chatgpt_slots/slot-blue state drain",
        ])
        flag = self.index(calls, lambda a: a[:2] == ["exec", "slot-blue"] and "touch" in a)
        self.assertEqual(calls[flag]["args"][-1], "/tmp/cheese-metering-draining")
        stop = self.index(calls, lambda a: a[0] == "compose" and "stop" in a and a[-1] == "slot-blue")
        polls = [i for i, c in enumerate(calls) if c["args"][:2] == ["exec", "slot-blue"] and "/proc/net/tcp" in c["args"]]
        # Stopped only after the slot itself counted no open connection.
        self.assertGreaterEqual(len(polls), 3)
        self.assertLess(up, flag)
        self.assertLess(max(polls), stop)
        self.assertEqual(self.state["running"], ["front", "slot-green"])

    def test_a_slot_that_fails_its_health_check_never_takes_traffic(self):
        result, calls = self.release(FAIL_UP="slot-green")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ready", " ".join(self.runtime(calls)))
        self.assertEqual(self.state["admin"]["front"]["slot-blue"], 0)
        self.assertIn("slot-blue", self.state["running"])
        self.assertFalse(any("touch" in c["args"] for c in calls))

    def test_the_drain_is_bounded(self):
        result, calls = self.release(
            open={"slot-blue:8444": [5]}, METERING_POLL_SECONDS="1", METERING_DRAIN_SECONDS="2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stopping it cuts them", result.stdout)
        self.assertNotIn("slot-blue", self.state["running"])

    def test_a_slot_switched_back_during_its_drain_keeps_running(self):
        result, calls = self.release(open={"slot-blue:8444": [5]}, SWITCH_BACK="blue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("serves again", result.stdout)
        self.assertIn("slot-blue", self.state["running"])

    def test_the_same_digest_and_configuration_is_left_serving(self):
        _result, first = self.release()
        label = next(c["config"] for c in first if c["args"][0] == "compose")
        result, calls = self.release(FAKE_CURRENT_IMAGE=DIGEST, FAKE_CONFIG=label)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no restart needed", result.stdout)
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))
        self.assertEqual(self.runtime(calls), [])
        # The login tool still arrives when the image does not change.
        self.assertTrue(self.installed_login)

    def test_a_changed_env_is_released_though_the_digest_is_unchanged(self):
        _result, first = self.release()
        label = next(c["config"] for c in first if c["args"][0] == "compose")
        result, calls = self.release(
            env_text=ENV_TEXT + "CHEESE_TOKEN_CAP=5000\n", FAKE_CURRENT_IMAGE=DIGEST, FAKE_CONFIG=label
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        up = [c for c in calls if c["args"][0] == "compose" and "up" in c["args"]]
        self.assertEqual(len(up), 1)
        self.assertNotEqual(up[0]["config"], label)

    def test_a_changed_front_is_never_recreated_without_acknowledgement(self):
        result, calls = self.release(WANTED_FRONT_HASH="new")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acknowledge the interruption", result.stderr)
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))
        result, calls = self.release(WANTED_FRONT_HASH="new", METERING_ALLOW_INTERRUPT="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state["started"][0], "front")

    def test_a_changed_front_configuration_is_reloaded_with_the_slots_state(self):
        result, calls = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.index(calls, lambda a: "/tmp/server-state" in " ".join(a))
        reload = self.index(calls, lambda a: a[:3] == ["kill", "--signal", "USR2"])
        self.assertLess(saved, reload)

    def test_a_pull_the_registry_drops_is_tried_again(self):
        result, calls = self.release(FAIL_PULLS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["args"][0] for c in calls].count("pull"), 3)

    def test_a_pull_that_never_succeeds_stops_the_release(self):
        result, calls = self.release(FAIL_PULLS="99")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([c["args"][0] for c in calls], ["pull"] * 4)

    def test_an_image_without_a_registry_digest_is_never_started(self):
        result, calls = self.release(FAKE_DIGEST="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))

    def test_a_release_creates_the_gateway_network_when_it_is_missing(self):
        for missing in ("", "1"):
            result, calls = self.release(NO_NETWORK=missing)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = [c["args"] for c in calls]
            created = ["network", "create", "--internal", "cheese-meter-gateway"]
            first_up = self.index(calls, lambda a: a[0] == "compose" and "up" in a)
            if missing:
                self.assertLess(args.index(created), first_up)
            else:
                self.assertNotIn(created, args)

    # ---- the one release that moves the box off the single container ----

    def cutover(self, **overrides):
        return self.release(running=(), legacy=True,
                            open={f"{LEGACY}:8444": [4, 2, 0], f"{LEGACY}:8445": [1, 0]}, **overrides)

    def test_the_cutover_steers_new_connections_and_lets_the_old_container_finish(self):
        result, calls = self.cutover()
        self.assertEqual(result.returncode, 0, result.stderr)
        rule = "-d 172.17.0.1/32 -p tcp --dport 8444 -m comment --comment cheese-metering-handover -j DNAT --to-destination 192.0.2.5:8444"
        self.assertEqual(self.sudo, [
            f"-n iptables -t nat -I PREROUTING 1 {rule}",
            f"-n iptables -t nat -I OUTPUT 1 {rule}",
            f"-n iptables -t nat -D PREROUTING {rule}",
            f"-n iptables -t nat -D OUTPUT {rule}",
        ])
        stopped = self.index(calls, lambda a: a[:2] == ["stop", LEGACY])
        polls = [i for i, c in enumerate(calls) if c["args"][:2] == ["exec", LEGACY]]
        self.assertLess(max(polls), stopped)
        front_up = self.index(calls, lambda a: a[0] == "compose" and "up" in a and "-d" in a and a[-1] == "front")
        self.assertLess(stopped, front_up)
        self.assertEqual(self.state["started"], ["slot-blue", "front-handover", "front"])
        self.assertFalse(self.state["legacy"])
        self.assertEqual(self.state["running"], ["slot-blue", "front"])

    def test_a_cutover_whose_front_fails_gives_the_address_back(self):
        result, calls = self.cutover(FAIL_UP="front")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.state["legacy_running"])
        self.assertEqual([line.split()[4] for line in self.sudo], ["-I", "-I", "-D", "-D"])

    def test_a_cutover_whose_steering_misses_the_handover_front_undoes_it(self):
        result, calls = self.cutover(PROBE_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([line.split()[4] for line in self.sudo], ["-I", "-I", "-D", "-D"])
        self.assertFalse(any(c["args"][:2] == ["stop", LEGACY] for c in calls))
        self.assertTrue(self.state["legacy_running"])

    def test_a_cutover_never_touches_a_container_of_another_project(self):
        result, calls = self.cutover(FAKE_PROJECT="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sudo, [])
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))

    def test_compose_runs_two_slots_behind_one_front(self):
        env = dict(
            os.environ,
            METERING_PROXY_IMAGE=DIGEST,
            METERING_PROXY_CONFIG_SHA256="c" * 64,
            METERING_FRONT_DIR="/front",
        )
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".env").write_text("")
            result = subprocess.run(
                ["docker", "compose", "--project-directory", folder, "--env-file", str(home / ".env"),
                 "-f", str(ROOT / "deploy/metering-proxy/compose.yml"), "--profile", "handover",
                 "config", "--format", "json"],
                env=env, capture_output=True, text=True, check=True,
            )
            config = json.loads(result.stdout)
            services = config["services"]
            for color in ("blue", "green"):
                slot = services[f"slot-{color}"]
                self.assertEqual(slot["image"], DIGEST)
                self.assertEqual(slot["labels"]["cheese.metering-proxy.config-sha256"], "c" * 64)
                # Nothing reaches a slot except through a front.
                self.assertNotIn("ports", slot)
                self.assertNotIn("meter-gateway", slot["networks"])
                mounts = {m["target"]: m["source"] for m in slot["volumes"]}
                self.assertNotIn("/addons", mounts)
                self.assertEqual(mounts["/var/log/cheese"], str(home / "logs"))
                self.assertEqual(mounts["/home/mitmproxy/.mitmproxy"], str(home / "certs"))
            # Only the front publishes, and only the CONNECT listener on the
            # bridge; the gateway reaches the ChatGPT one by the alias.
            self.assertEqual([(p["host_ip"], p["published"], p["target"]) for p in services["front"]["ports"]],
                             [("172.17.0.1", "8444", 8444)])
            self.assertEqual(services["front"]["networks"]["meter-gateway"]["aliases"], ["metering-proxy"])
            self.assertEqual(config["networks"]["meter-gateway"]["name"], "cheese-meter-gateway")
            self.assertTrue(config["networks"]["meter-gateway"]["external"])

    def test_the_front_configuration_is_valid(self):
        cfg = ROOT / "deploy/metering-proxy/front.cfg"
        image = yaml.safe_load((ROOT / "deploy/metering-proxy/compose.yml").read_text())["x-front"]["image"]
        subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "-v", f"{cfg}:/c.cfg:ro",
             "-e", "METERING_BLUE_ADDR=10.213.49.2", "-e", "METERING_GREEN_ADDR=10.213.49.3",
             "--entrypoint", "haproxy", image, "-c", "-f", "/c.cfg"],
            check=True, capture_output=True,
        )


class MeteringWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-metering-proxy.yml").read_text()
        )

    def test_only_main_runs_on_dev_and_interruption_is_the_operators_call(self):
        job = self.workflow["jobs"]["release"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        release = next(s for s in job["steps"] if s.get("name") == "Release the verified image")
        self.assertEqual(
            release["env"]["METERING_ALLOW_INTERRUPT"], "${{ inputs.interrupt && '1' || '0' }}"
        )
        self.assertEqual(job["runs-on"], ["self-hosted", "cheese-dev"])
        self.assertEqual(
            self.workflow["permissions"],
            {"contents": "read", "actions": "read", "packages": "read"},
        )

    def test_gate_checks_exact_sha_before_checkout_or_release(self):
        steps = self.workflow["jobs"]["release"]["steps"]
        gate = next(s["run"] for s in steps if s.get("name", "").startswith("Verify"))
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for command in ["git", "python3"]:
                path = home / command
                path.write_text(
                    '#!/bin/sh\necho "' + command + ' $*" >> "$CALLS"\n'
                    'case "$*" in\n'
                    '  *--require-ci*) exit "$CI_STATUS";;\n'
                    '  *--is-ancestor*) exit "$ANCESTOR_STATUS";;\n'
                    "esac\n"
                )
                path.chmod(0o755)
            for sha, ancestor, ci, success in [
                ("a" * 40, "0", "0", True),
                ("bad;echo secret", "0", "0", False),
                ("a" * 40, "1", "0", False),
                ("a" * 40, "0", "1", False),
            ]:
                calls = home / "calls"
                calls.write_text("")
                env = dict(
                    os.environ,
                    PATH=f"{home}:{os.environ['PATH']}",
                    RELEASE_SHA=sha,
                    CALLS=str(calls),
                    CI_STATUS=ci,
                    ANCESTOR_STATUS=ancestor,
                )
                result = subprocess.run(
                    ["bash", "-c", gate], env=env, capture_output=True, text=True
                )
                self.assertEqual(result.returncode == 0, success)
                recorded = calls.read_text().splitlines()
                if success:
                    self.assertEqual(
                        recorded,
                        [
                            "git merge-base --is-ancestor " + sha + " origin/main",
                            "python3 deploy/check-auto-deploy.py --require-ci " + sha,
                            "git checkout --detach " + sha,
                        ],
                    )
                else:
                    self.assertFalse(any("checkout" in line for line in recorded))

    def test_dev_deployment_requires_exact_sha_ci_before_metering_release(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/deploy-dev.yml").read_text()
        )
        step = next(
            step
            for step in workflow["jobs"]["deploy"]["steps"]
            if step.get("name") == "Release the verified metering image"
        )
        self.assertEqual(step["if"], "steps.release.outputs.skip != 'true'")
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")
        # An automatic release never interrupts a stream: a change that would
        # (the front itself) fails it instead, for an operator to run.
        self.assertNotIn("METERING_ALLOW_INTERRUPT", step["env"])
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "git").write_text(
                '#!/bin/sh\ncase "$*" in\n'
                '  "rev-parse HEAD") printf "%s\\n" "$TEST_SHA";;\n'
                '  *) printf "%s\\n" "bad-or-short-sha";;\nesac\n'
            )
            (home / "python3").write_text(
                '#!/bin/sh\nprintf "gate %s\\n" "$*" >> "$CALLS"\nexit "$CI_STATUS"\n'
            )
            (home / "bash").write_text(
                '#!/bin/sh\nprintf "release %s\\n" "$*" >> "$CALLS"\n'
            )
            for path in [home / "git", home / "python3", home / "bash"]:
                path.chmod(0o755)
            for status in ["0", "1"]:
                calls = home / "calls"
                calls.write_text("")
                env = dict(
                    os.environ,
                    PATH=f"{home}:{os.environ['PATH']}",
                    CALLS=str(calls),
                    TEST_SHA="c" * 40,
                    CI_STATUS=status,
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", step["run"]],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode == 0, status == "0")
                expected = ["gate deploy/check-auto-deploy.py --require-ci " + "c" * 40]
                if status == "0":
                    expected.append(
                        "release deploy/release-metering-proxy.sh " + "c" * 40
                    )
                self.assertEqual(calls.read_text().splitlines(), expected)

    def test_build_uses_full_sha_for_metering_and_promotion(self):
        text = (ROOT / ".github/workflows/build.yml").read_text()
        build = yaml.safe_load(text)["jobs"]["build-metering-proxy"]
        metadata = next(s for s in build["steps"] if s.get("id") == "meta")
        self.assertEqual(
            metadata["with"]["tags"].strip(), "type=sha,prefix=,format=long"
        )
        self.assertIn('promote metering-proxy "$BASE_SHA" "$CURRENT_SHA"', text)


if __name__ == "__main__":
    unittest.main()
