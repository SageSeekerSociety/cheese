"""Exercise the gateway's blue-green release against a fake docker, compose and proxy."""

import hashlib
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release-gateway.sh"
PROXY_CONFIG = ROOT / "deploy/compose/gateway-proxy.cfg"
LEGACY = "cheese-gateway-litellm-1"

# A docker CLI whose containers, proxy servers and open sessions live in a JSON
# file. Every call is appended to CALLS. A container's id is "<service>-id".
FAKE = r"""#!/usr/bin/env python3
import fcntl, json, os, pathlib, sys
args = sys.argv[1:]
state_path = pathlib.Path(os.environ["STATE"])
stdin = sys.stdin.read() if ("exec" in args and "-i" in args) or "psql" in args else ""
# The release pipes one docker call into another; one at a time on the state,
# taken after stdin so the reading end never holds it while the writer waits.
lock = open(str(state_path) + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
state = json.loads(state_path.read_text())
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin}) + "\n")
out = ""
code = 0
running = state["running"]
env = os.environ

def service(container):
    return container[: -len("-id")] if container.endswith("-id") else container

if args[:2] == ["network", "inspect"] and env.get("NO_NETWORK"):
    code = 1
elif args[:2] == ["image", "inspect"]:
    out = "a" * 40
elif args[0] == "create":
    out = "candidate"
elif args[0] == "cp":
    pathlib.Path(args[-1]).write_text(env.get("NEW_CONFIG", "model_list: []\n"))
elif args[0] == "run" and env.get("FAIL_PREFLIGHT"):
    code = 1
elif args[0] == "inspect":
    name, fmt = args[1], args[-1]
    if name == LEGACY and not state.get("legacy"):
        code = 1
    elif "State.Running" in fmt:
        exits = state.setdefault("exits_after", {})
        if service(name) in exits:
            exits[service(name)] -= 1
            if exits[service(name)] < 0 and service(name) in running:
                running.remove(service(name))
        out = "true" if service(name) in running else "false"
    elif "State.Pid" in fmt:
        out = "4242"
    elif "Created" in fmt:
        out = env.get("CREATED_" + service(name).upper().replace("-", "_"), "2026-01-01T00:00:00Z")
    elif "config-hash" in fmt:
        out = env.get("RUNNING_HASH_" + service(name).upper().replace("-", "_"), "hash")
    elif "Mounts" in fmt:
        out = env.get("SERVING_CONFIG", "")
    else:
        out = "sha256:" + name
elif args[0] in ("stop", "start", "rm") and args[-1] == LEGACY:
    state["legacy"] = args[0] == "start" or (args[0] == "stop" and False)
    if args[0] == "stop":
        state["legacy"] = "stopped"
    if args[0] == "rm":
        state["legacy"] = False
elif args[0] == "kill":
    if "USR1" in args:
        exits = state.setdefault("exits_after", {})
        exits.setdefault(service(args[-1]), int(env.get("OLD_PROXY_POLLS", "0")))
elif args[0] == "exec" and "socat" in args:
    for command in stdin.strip().split(";"):
        words = command.split()
        if words[:2] == ["set", "server"]:
            server = words[2].split("/")[1]
            state["admin"][server] = 8 if words[-1] == "drain" else 0
        elif words[:3] == ["show", "servers", "state"]:
            out += "1\n# be_id be_name srv_id srv_name srv_addr srv_op_state srv_admin_state\n"
            for color in ("blue", "green"):
                op = 2 if "litellm-" + color in running else 0
                if color == env.get("NEVER_UP") or (env.get("NEW_PROXY_SEES_DOWN") and args[2] == "gateway-proxy-b-id"):
                    op = 0
                out += f"3 litellm 1 litellm-{color} 10.0.0.1 {op} {state['admin'].get('litellm-' + color, 0)}\n"
        elif words[:2] == ["show", "stat"]:
            out += "# pxname,svname,qcur,qmax,scur\n"
            for color in ("blue", "green"):
                queue = state["sessions"].get(color, [])
                open_now = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else 0)
                state["sessions"][color] = queue
                if env.get("SWITCH_BACK") == color and state["admin"].get("litellm-" + color) == 8:
                    state["admin"]["litellm-" + color] = 0
                out += f"litellm,litellm-{color},0,0,{open_now}\n"
elif args[0] == "exec" and any("v1/models" in a for a in args):
    who = service(args[1]).upper().replace("-", "_")
    out = env.get("MODELS_" + who, "fake\nglm-5.2").replace(",", "\n")
elif args[0] == "exec":
    if env.get("FAIL_READY") and args[1].startswith(env["FAIL_READY"]):
        code = 1
elif args[0] == "compose":
    rest = args[args.index("-p") + 2:]
    names = [a for a in rest[1:] if not a.startswith("-") and not a.isdigit()]
    if rest[:1] == ["ps"]:
        out = f"{names[-1]}-id" if names[-1] in running else ""
    elif rest[:2] == ["config", "--hash"]:
        svc = rest[2]
        out = svc + " " + env.get("WANTED_HASH_" + svc.upper().replace("-", "_"), "hash")
    elif rest[:2] == ["config", "--images"]:
        out = "haproxy:test"
    elif rest[:3] == ["config", "--format", "json"]:
        out = json.dumps({"services": {"gateway-proxy-a": {"image": env.get("PROXY_IMAGE", "haproxy:test")}}})
    elif rest[:1] == ["up"]:
        failing = env.get("FAIL_UP", "")
        if "--no-start" in rest:
            pass
        elif env.get("HANG_UP") and env["HANG_UP"] in names:
            state_path.write_text(json.dumps(state))
            lock.close()
            import time
            time.sleep(60)
        elif failing and failing in names:
            code = 1
        else:
            running.extend(s for s in names if s not in running)
    elif rest[:1] in (["stop"], ["rm"]):
        for s in names:
            if s in running:
                running.remove(s)
state_path.write_text(json.dumps(state))
if out:
    print(out)
sys.exit(code)
""".replace("LEGACY", repr(LEGACY))

# `sudo -n nsenter ... ss -Htin`: one tcp-info line per connection, taken from
# BUSY (a list of lastsnd values in ms, one call's worth per ";").
FAKE_SUDO = r"""#!/usr/bin/env python3
import os, pathlib
queue_path = pathlib.Path(os.environ["STATE"] + ".busy")
calls = queue_path.read_text().split(";") if queue_path.exists() else [""]
now = calls[0]
queue_path.write_text(";".join(calls[1:] or [now]))
for last in filter(None, now.split(",")):
    print(f"ESTAB 0 0 10.0.0.1:4000 10.0.0.9:5000")
    print(f"\t cubic lastsnd:{last} lastrcv:{last} lastack:{last}")
"""

SERVING = ("gateway-netns", "gateway-proxy-a", "litellm-blue")
APP = ("gateway-netns", "gateway-proxy-a", "gateway-proxy-b", "litellm-blue", "litellm-green")


class GatewayReleaseTest(unittest.TestCase):
    def run_release(self, serving=SERVING, legacy=False, sessions=None, proxy_changed=False, busy="",
                    admin=None, stale_previous=None, cancel_when=None, installed_config=None, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "compose").mkdir()
            (root / "compose/.env").write_text("TEST=1\n")
            if not proxy_changed or installed_config is not None:
                (root / "proxy").mkdir()
                (root / "proxy/haproxy.cfg").write_text(installed_config or PROXY_CONFIG.read_text())
            # What the release recorded when it started the running proxy.
            (root / "proxy-state").mkdir()
            recorded = json.dumps({"image": "haproxy:test"}, sort_keys=True).encode()
            (root / "proxy-state/proxy-definition").write_text(hashlib.sha256(recorded).hexdigest() + "\n")
            for name, body in (("docker", FAKE), ("sudo", FAKE_SUDO)):
                (root / name).write_text(body)
                (root / name).chmod(0o755)
            if stale_previous is not None:
                (root / "proxy").mkdir(exist_ok=True)
                (root / "proxy/haproxy.cfg.previous").write_text(stale_previous)
            state = {"running": list(serving), "legacy": legacy, "admin": admin or {}, "sessions": sessions or {}}
            (root / "state").write_text(json.dumps(state))
            if busy:
                (root / "state.busy").write_text(busy)
            env = dict(
                os.environ,
                PATH=f"{root}:{os.environ['PATH']}",
                GATEWAY_HOME=folder,
                GITHUB_ACTIONS="true",
                GITHUB_RUN_ID="123",
                GITHUB_RUN_ATTEMPT="1",
                GATEWAY_POLL_SECONDS="0",
                GATEWAY_MODELS_WAIT_SECONDS="1",
                GATEWAY_PROXY_EXIT_SECONDS="1",
                CALLS=str(root / "calls"),
                STATE=str(root / "state"),
            )
            env.update(overrides)
            if cancel_when is None:
                result = subprocess.run(
                    ["bash", str(SCRIPT), "a" * 40], env=env, capture_output=True, text=True
                )
            else:
                # The workflow cancelled: its whole process group is signalled.
                process = subprocess.Popen(
                    ["bash", str(SCRIPT), "a" * 40], env=env, text=True, start_new_session=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                deadline = time.time() + 30
                while time.time() < deadline and not (
                    (root / "calls").exists() and cancel_when((root / "calls").read_text())
                ):
                    time.sleep(0.1)
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=30)
                result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            calls = (
                [json.loads(line) for line in (root / "calls").read_text().splitlines()]
                if (root / "calls").exists()
                else []
            )
            final = json.loads((root / "state").read_text())
            # The gateway as callers meet it; the database and Redis are left out.
            final["running"] = sorted(s for s in final["running"] if s in APP)
            final["installed_config"] = (
                (root / "proxy/haproxy.cfg").read_text() if (root / "proxy/haproxy.cfg").exists() else None
            )
            final["state_file_left"] = (root / "proxy-state/servers.state").exists()
            final["backup_left"] = (root / "proxy/haproxy.cfg.previous").exists()
            previous = list(root.glob("releases/*/previous-image"))
            final["previous_image"] = previous[0].read_text().strip() if previous else None
            return result, calls, final

    @staticmethod
    def index(calls, predicate):
        return next(i for i, c in enumerate(calls) if predicate(c["args"], c["stdin"]))

    @staticmethod
    def switched_to(color):
        return lambda args, stdin: f"litellm/litellm-{color} state ready" in stdin

    @staticmethod
    def stopped(service):
        return lambda args, stdin: args[0] == "compose" and "stop" in args and args[-1] == service

    @staticmethod
    def started(service):
        return lambda args, stdin: (
            args[0] == "compose" and "up" in args and "--no-start" not in args and service in args
        )

    @staticmethod
    def soft_stopped(container):
        return lambda args, stdin: args[0] == "kill" and "USR1" in args and args[-1] == container

    # ---- The release itself ----

    def test_only_the_release_workflow_may_run_it(self):
        result, calls, _ = self.run_release(GITHUB_ACTIONS="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failed_image_test_never_changes_live_service(self):
        result, calls, state = self.run_release(FAIL_PREFLIGHT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))

    def test_traffic_moves_only_once_the_new_color_is_ready_and_old_stops_after(self):
        result, calls, state = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self.index(calls, self.started("litellm-green"))
        switch = self.index(calls, self.switched_to("green"))
        stop = self.index(calls, self.stopped("litellm-blue"))
        self.assertLess(start, switch)
        self.assertLess(switch, stop)
        # The switch drains the old color in the same command.
        self.assertIn("litellm/litellm-blue state drain", calls[switch]["stdin"])
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-green"])
        # Neither the address nor an unchanged proxy is touched.
        self.assertFalse(any(self.started("gateway-netns")(c["args"], "") for c in calls))
        self.assertFalse(any(self.started("gateway-proxy-b")(c["args"], "") for c in calls))

    def test_the_next_release_goes_back_to_the_other_color(self):
        result, calls, state = self.run_release(serving=("gateway-netns", "gateway-proxy-a", "litellm-green"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(self.index(calls, self.started("litellm-blue")), self.index(calls, self.switched_to("blue")))
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-blue"])

    def test_after_a_restart_mid_drain_the_newer_color_keeps_serving(self):
        result, calls, state = self.run_release(
            serving=SERVING + ("litellm-green",),
            CREATED_LITELLM_BLUE="2026-01-01T00:00:00Z",
            CREATED_LITELLM_GREEN="2026-01-02T00:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Green, the last release's, serves throughout; stale blue is drained,
        # replaced and only then takes the traffic.
        drained = self.index(calls, self.stopped("litellm-blue"))
        restarted = self.index(calls, self.started("litellm-blue"))
        self.assertLess(drained, restarted)
        self.assertLess(restarted, self.index(calls, self.switched_to("blue")))
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-blue"])

    def test_a_new_color_that_is_not_healthy_or_not_ready_never_takes_traffic(self):
        for failure in ({"FAIL_UP": "litellm-green"}, {"FAIL_READY": "litellm-green"}):
            with self.subTest(failure):
                result, calls, state = self.run_release(**failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("state ready" in c["stdin"] for c in calls))
                self.assertFalse(any(self.stopped("litellm-blue")(c["args"], "") for c in calls))
                self.assertEqual(state["running"], sorted(SERVING))
                self.assertNotEqual(state["admin"].get("litellm-blue"), 8)

    def test_a_new_color_missing_a_model_stored_in_the_database_never_takes_traffic(self):
        result, calls, state = self.run_release(
            MODELS_LITELLM_BLUE="fake,glm-5.2", MODELS_LITELLM_GREEN="glm-5.2"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("state ready" in c["stdin"] for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))
        # Asked through a listing, never /health, which calls the providers.
        listing = [c for c in calls if c["args"][0] == "exec" and "v1/models" in " ".join(c["args"])]
        self.assertTrue(listing)
        self.assertFalse(any("/health'" in " ".join(c["args"]) for c in calls))

    def test_a_model_this_release_removes_from_the_configuration_does_not_hold_it(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as old:
            old.write("model_list:\n  - model_name: glm-4.5\n  - model_name: glm-5.2\n")
            old.flush()
            result, _, state = self.run_release(
                MODELS_LITELLM_BLUE="fake,glm-4.5,glm-5.2",
                MODELS_LITELLM_GREEN="fake,glm-5.2",
                SERVING_CONFIG=old.name,
                NEW_CONFIG="model_list:\n  - model_name: glm-5.2\n",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-green"])

    def test_the_old_color_is_stopped_only_once_its_requests_are_done(self):
        result, calls, _ = self.run_release(sessions={"blue": [2, 1, 1, 0]})
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.index(calls, self.stopped("litellm-blue"))
        polls = [c for c in calls[:stop] if "show stat" in c["stdin"]]
        self.assertGreaterEqual(len(polls), 4)

    def test_requests_still_open_at_the_drain_cap_are_cut_and_counted_out_loud(self):
        result, calls, state = self.run_release(sessions={"blue": [3]}, GATEWAY_DRAIN_SECONDS="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("litellm-blue", state["running"])
        self.assertIn("::warning::", result.stdout)
        self.assertIn("3 open requests", result.stdout)

    def test_a_color_switched_back_during_its_drain_keeps_running(self):
        result, calls, state = self.run_release(sessions={"blue": [1]}, SWITCH_BACK="blue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("litellm-blue", state["running"])

    def test_a_healthy_release_builds_the_spend_log_index_without_blocking_inserts(self):
        result, calls, _ = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        build = calls[-1]
        self.assertIn("exec", build["args"])
        self.assertIn("litellm-db", build["args"])
        self.assertIn(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_api_key_startTime_idx"',
            build["stdin"],
        )

    def test_a_failed_release_builds_no_index(self):
        result, calls, _ = self.run_release(FAIL_UP="litellm-green")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("psql" in c["args"] for c in calls))

    def test_the_network_shared_with_the_metering_proxy_exists_before_rollout(self):
        for missing in ("", "1"):
            result, calls, _ = self.run_release(NO_NETWORK=missing)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = [c["args"] for c in calls]
            created = ["network", "create", "--internal", "cheese-meter-gateway"]
            compose = next(i for i, a in enumerate(args) if a[0] == "compose")
            if missing:
                self.assertLess(args.index(created), compose)
            else:
                self.assertNotIn(created, args)

    # ---- A change to the proxy ----

    def test_a_changed_proxy_is_replaced_beside_the_running_one(self):
        for change in ({"proxy_changed": True}, {"PROXY_IMAGE": "haproxy:newer"}):
            with self.subTest(change):
                result, calls, state = self.run_release(**change)
                self.assertEqual(result.returncode, 0, result.stderr)
                started = self.index(calls, self.started("gateway-proxy-b"))
                soft = self.index(calls, self.soft_stopped("gateway-proxy-a-id"))
                self.assertLess(started, soft)
                # Its exit stays an exit.
                self.assertTrue(any(c["args"][:3] == ["update", "--restart=no", "gateway-proxy-a-id"] for c in calls))
                # The address is never recreated for it.
                self.assertFalse(any(self.started("gateway-netns")(c["args"], "") for c in calls))
                self.assertIn("gateway-proxy-b", state["running"])

    def test_an_unchanged_proxy_is_left_alone(self):
        result, calls, _ = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(self.started("gateway-proxy-b")(c["args"], "") for c in calls))
        self.assertFalse(any(c["args"][0] == "kill" for c in calls))

    def test_a_soft_stopped_proxy_holds_the_old_color_until_it_exits(self):
        # The old proxy still carries streams the new one cannot count.
        result, calls, _ = self.run_release(proxy_changed=True, OLD_PROXY_POLLS="4")
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.index(calls, self.stopped("litellm-blue"))
        asked = [
            c for c in calls[:stop]
            if c["args"][0] == "inspect" and c["args"][1] == "gateway-proxy-a-id" and "State.Running" in c["args"][-1]
        ]
        self.assertGreaterEqual(len(asked), 5)

    def test_a_new_proxy_that_does_not_come_up_leaves_the_running_one_serving(self):
        result, calls, state = self.run_release(proxy_changed=True, FAIL_UP="gateway-proxy-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(self.soft_stopped("gateway-proxy-a-id")(c["args"], "") for c in calls))
        self.assertFalse(any("state ready" in c["stdin"] for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))

    def test_a_changed_address_is_replaced_only_with_acknowledged_interruption(self):
        result, calls, state = self.run_release(WANTED_HASH_GATEWAY_NETNS="new")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))


    # ---- Guards the review found no test for ----

    def test_the_new_color_is_drained_before_it_starts(self):
        result, calls, _ = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        drained = self.index(calls, lambda a, s: "litellm/litellm-green state drain" in s)
        self.assertLess(drained, self.index(calls, self.started("litellm-green")))

    def test_traffic_never_moves_to_a_color_the_proxy_does_not_see_up(self):
        result, calls, state = self.run_release(NEVER_UP="green", GATEWAY_PROXY_UP_SECONDS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("state ready" in c["stdin"] for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))

    def test_the_serving_image_is_recorded_for_a_rollback(self):
        result, _, state = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["previous_image"], "sha256:litellm-blue-id")

    def test_the_serving_color_comes_from_the_proxy_not_a_health_sample(self):
        # Blue serves (ready in the proxy) but misses its checks right now; green
        # is stopped and drained. The release must still treat blue as serving.
        result, calls, state = self.run_release(NEVER_UP="blue", admin={"litellm-green": 8}, GATEWAY_PROXY_UP_SECONDS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        switch = self.index(calls, self.switched_to("green"))
        self.assertIn("litellm/litellm-blue state drain", calls[switch]["stdin"])

    def test_a_running_color_with_none_ready_in_the_proxy_changes_nothing(self):
        result, calls, state = self.run_release(admin={"litellm-blue": 8, "litellm-green": 8})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))
        self.assertEqual(state["running"], sorted(SERVING))

    def test_of_two_ready_colors_the_newer_one_keeps_serving(self):
        for newer, older in (("green", "blue"), ("blue", "green")):
            with self.subTest(newer=newer):
                result, calls, state = self.run_release(
                    serving=SERVING + ("litellm-green",),
                    **{f"CREATED_LITELLM_{newer.upper()}": "2026-01-02T00:00:00Z",
                       f"CREATED_LITELLM_{older.upper()}": "2026-01-01T00:00:00Z"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(self.index(calls, self.stopped(f"litellm-{older}")), self.index(calls, self.switched_to(older)))

    def test_a_proxy_left_from_an_interrupted_change_is_soft_stopped(self):
        result, calls, _ = self.run_release(
            serving=SERVING + ("gateway-proxy-b",), GATEWAY_DRAIN_SECONDS="0",
            CREATED_GATEWAY_PROXY_A="2026-01-02T00:00:00Z",
            CREATED_GATEWAY_PROXY_B="2026-01-01T00:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c["args"][:3] == ["update", "--restart=no", "gateway-proxy-b-id"] for c in calls))
        self.assertTrue(any(self.soft_stopped("gateway-proxy-b-id")(c["args"], "") for c in calls))

    def test_a_proxy_change_waits_while_a_replaced_proxy_still_runs(self):
        result, calls, state = self.run_release(
            serving=SERVING + ("gateway-proxy-b",), proxy_changed=True, OLD_PROXY_POLLS="99",
            CREATED_GATEWAY_PROXY_A="2026-01-02T00:00:00Z",
            CREATED_GATEWAY_PROXY_B="2026-01-01T00:00:00Z",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(self.started("gateway-proxy-b")(c["args"], "") for c in calls))
        self.assertFalse(any("state ready" in c["stdin"] for c in calls))

    def test_a_new_proxy_that_does_not_see_the_serving_color_is_rolled_back(self):
        result, calls, state = self.run_release(proxy_changed=True, NEW_PROXY_SEES_DOWN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(self.soft_stopped("gateway-proxy-a-id")(c["args"], "") for c in calls))
        self.assertNotIn("gateway-proxy-b", state["running"])

    def test_a_failed_proxy_change_puts_back_the_configuration_it_replaced(self):
        running = "global\n  # the configuration the running proxy was started with\n"
        result, _, state = self.run_release(installed_config=running, FAIL_UP="gateway-proxy-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["installed_config"], running)
        self.assertFalse(state["backup_left"])

    def test_a_replaced_proxy_that_never_exits_leaves_the_old_color_draining(self):
        result, calls, state = self.run_release(proxy_changed=True, OLD_PROXY_POLLS="99999", GATEWAY_DRAIN_SECONDS="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::", result.stdout)
        self.assertIn("litellm-blue", state["running"])
        self.assertFalse(any(self.stopped("litellm-blue")(c["args"], "") for c in calls))

    def test_a_failed_definition_change_never_restores_a_stale_configuration(self):
        result, _, state = self.run_release(
            PROXY_IMAGE="haproxy:newer", FAIL_UP="gateway-proxy-b", stale_previous="global\n  # stale\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["installed_config"], PROXY_CONFIG.read_text())

    def test_a_successful_proxy_change_leaves_no_backup_and_no_state_file(self):
        result, _, state = self.run_release(installed_config="global\n  # the previous configuration\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(state["backup_left"])
        self.assertFalse(state["state_file_left"])

    def test_no_server_state_file_survives_a_release(self):
        # Any later cold start of the proxy loads it, drain flags included.
        for case in ({}, {"proxy_changed": True}, {"proxy_changed": True, "FAIL_UP": "gateway-proxy-b"},
                     {"proxy_changed": True, "NEW_PROXY_SEES_DOWN": "1"}):
            with self.subTest(case):
                _, _, state = self.run_release(**case)
                self.assertFalse(state["state_file_left"])

    # ---- Once: from the single container ----

    def test_moving_from_the_single_container_needs_acknowledged_interruption(self):
        result, calls, state = self.run_release(serving=(), legacy=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state["legacy"])
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))

    def test_moving_from_the_single_container_stops_it_only_once_blue_is_ready_and_it_is_quiet(self):
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1", busy="1000;2000;90000;"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        blue = self.index(calls, self.started("litellm-blue"))
        stop = self.index(calls, lambda a, s: a[0] == "stop" and a[-1] == LEGACY)
        proxy = self.index(calls, self.started("gateway-proxy-a"))
        self.assertLess(blue, stop)
        self.assertLess(stop, proxy)
        self.assertIn("has no request in flight", result.stdout)
        self.assertFalse(state["legacy"])
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-blue"])

    def test_a_single_container_that_never_quiets_is_stopped_at_the_wait_cap(self):
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1", busy="1000",
            GATEWAY_MIGRATION_QUIET_SECONDS="0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stopping it anyway", result.stdout)
        self.assertEqual(state["running"], ["gateway-netns", "gateway-proxy-a", "litellm-blue"])

    def test_a_cancelled_move_gives_the_single_container_back(self):
        # Cancelled after the single container stopped, before the proxy is up.
        def proxy_starting(calls):
            return any(
                '"up"' in line and '"gateway-proxy-a"' in line and '"--no-start"' not in line
                for line in calls.splitlines()
            )
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1", HANG_UP="gateway-proxy-a",
            cancel_when=proxy_starting,
        )
        self.assertNotEqual(result.returncode, 0)
        stop = self.index(calls, lambda a, s: a[0] == "stop" and a[-1] == LEGACY)
        self.assertTrue(any(c["args"] == ["start", LEGACY] for c in calls[stop:]))

    def test_a_proxy_that_does_not_come_up_gives_the_single_container_back(self):
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1", FAIL_UP="gateway-proxy-a"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(c["args"] == ["start", LEGACY] for c in calls))
        self.assertEqual(state["running"], [])


if __name__ == "__main__":
    unittest.main()
