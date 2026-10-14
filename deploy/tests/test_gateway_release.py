"""Exercise the gateway's blue-green release against a fake docker, compose and proxy."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "release-gateway.sh"
LEGACY = "cheese-gateway-litellm-1"

# A docker CLI whose containers, proxy servers and open sessions live in a JSON
# file. Every call is appended to CALLS.
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
    f.write(json.dumps({"args": args, "image": os.environ.get("GATEWAY_IMAGE"), "stdin": stdin}) + "\n")
out = ""
code = 0
running = state["running"]

def save():
    state_path.write_text(json.dumps(state))

if args[:2] == ["network", "inspect"] and os.environ.get("NO_NETWORK"):
    code = 1
elif args[:2] == ["image", "inspect"]:
    out = "a" * 40
elif args[0] == "create":
    out = "candidate"
elif args[0] == "cp":
    pathlib.Path(args[-1]).write_text("model_list: []\n")
elif args[0] == "run" and os.environ.get("FAIL_PREFLIGHT"):
    code = 1
elif args[0] == "inspect":
    name = args[1]
    if name == "proxy-id":
        out = os.environ.get("RUNNING_PROXY_HASH", "hash")
    elif name == LEGACY:
        code = 0 if state.get("legacy") else 1
    elif "{{.Created}}" in args:
        out = os.environ.get("CREATED_" + name.split("-")[1].upper(), "2026-01-01T00:00:00Z")
    else:
        out = "sha256:" + name
elif args[0] in ("stop", "start", "rm") and args[-1] == LEGACY:
    state["legacy"] = args[0] != "rm"
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
                out += f"3 litellm 1 litellm-{color} 10.0.0.1 {op} {state['admin'].get('litellm-' + color, 0)}\n"
        elif words[:2] == ["show", "stat"]:
            out += "# pxname,svname,qcur,qmax,scur\n"
            for color in ("blue", "green"):
                queue = state["sessions"].get(color, [])
                open_now = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else 0)
                state["sessions"][color] = queue
                if os.environ.get("SWITCH_BACK") == color and state["admin"].get("litellm-" + color) == 8:
                    state["admin"]["litellm-" + color] = 0
                out += f"litellm,litellm-{color},0,0,{open_now}\n"
elif args[0] == "exec" and "pgrep" in args:
    workers = state.setdefault("workers", [2])
    out = "\n".join(["1"] * (workers.pop(0) if len(workers) > 1 else workers[0]))
elif args[0] == "exec":
    if os.environ.get("FAIL_READY") and args[1].endswith(os.environ["FAIL_READY"]):
        code = 1
elif args[0] == "compose":
    rest = args[args.index("-p") + 2:]
    if rest[:1] == ["ps"]:
        service = rest[-1]
        if service == "gateway-proxy":
            out = "proxy-id" if "gateway-proxy" in running else ""
        else:
            out = f"{service}-id" if service in running else ""
    elif rest[:2] == ["config", "--hash"]:
        out = "gateway-proxy hash"
    elif rest[:2] == ["config", "--images"]:
        out = "haproxy:test"
    elif rest[:1] == ["up"]:
        services = [a for a in rest[1:] if not a.startswith("-") and not a.isdigit()]
        failing = os.environ.get("FAIL_UP", "")
        if "--no-start" in rest:
            pass
        elif failing and failing in services:
            code = 1
        else:
            running.extend(s for s in services if s not in running)
    elif rest[:1] in (["stop"], ["rm"]):
        for s in rest[1:]:
            if s in running:
                running.remove(s)
save()
if out:
    print(out)
sys.exit(code)
""".replace("LEGACY", repr(LEGACY))


class GatewayReleaseTest(unittest.TestCase):
    def run_release(self, serving=("gateway-proxy", "litellm-blue"), legacy=False, sessions=None, workers=None, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "compose").mkdir()
            (root / "compose/.env").write_text("TEST=1\n")
            docker = root / "docker"
            docker.write_text(FAKE)
            docker.chmod(0o755)
            state = {
                "running": list(serving),
                "legacy": legacy,
                "admin": {},
                "sessions": sessions or {},
                "workers": workers or [2],
            }
            (root / "state").write_text(json.dumps(state))
            env = dict(
                os.environ,
                PATH=f"{root}:{os.environ['PATH']}",
                GATEWAY_HOME=folder,
                GITHUB_ACTIONS="true",
                GITHUB_RUN_ID="123",
                GITHUB_RUN_ATTEMPT="1",
                GATEWAY_POLL_SECONDS="0",
                CALLS=str(root / "calls"),
                STATE=str(root / "state"),
            )
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(SCRIPT), "a" * 40], env=env, capture_output=True, text=True
            )
            calls = (
                [json.loads(line) for line in (root / "calls").read_text().splitlines()]
                if (root / "calls").exists()
                else []
            )
            final = json.loads((root / "state").read_text())
            # The gateway as callers meet it; the database and Redis are left out.
            final["running"] = sorted(
                s for s in final["running"] if s in ("gateway-proxy", "litellm-blue", "litellm-green")
            )
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
        return lambda args, stdin: args[0] == "compose" and "up" in args and args[-1] == service

    def test_only_the_release_workflow_may_run_it(self):
        result, calls, _ = self.run_release(GITHUB_ACTIONS="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failed_image_test_never_changes_live_service(self):
        result, calls, state = self.run_release(FAIL_PREFLIGHT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])

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
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-green"])
        # Never the proxy: it carries every open stream.
        self.assertFalse(any(self.started("gateway-proxy")(c["args"], "") for c in calls))

    def test_the_next_release_goes_back_to_the_other_color(self):
        result, calls, state = self.run_release(serving=("gateway-proxy", "litellm-green"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(self.index(calls, self.started("litellm-blue")), self.index(calls, self.switched_to("blue")))
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])

    def test_after_a_restart_mid_drain_the_newer_color_keeps_serving(self):
        result, calls, state = self.run_release(
            serving=("gateway-proxy", "litellm-blue", "litellm-green"),
            CREATED_BLUE="2026-01-01T00:00:00Z",
            CREATED_GREEN="2026-01-02T00:00:00Z",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Green, the last release's, serves throughout; stale blue is drained,
        # replaced and only then takes the traffic.
        drained = self.index(calls, self.stopped("litellm-blue"))
        restarted = self.index(calls, self.started("litellm-blue"))
        self.assertLess(drained, restarted)
        self.assertLess(restarted, self.index(calls, self.switched_to("blue")))
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])

    def test_a_new_color_that_is_not_healthy_or_not_ready_never_takes_traffic(self):
        for failure in ({"FAIL_UP": "litellm-green"}, {"FAIL_READY": "litellm-green-id"}):
            with self.subTest(failure):
                result, calls, state = self.run_release(**failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("state ready" in c["stdin"] for c in calls))
                self.assertFalse(any(self.stopped("litellm-blue")(c["args"], "") for c in calls))
                self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])
                self.assertNotEqual(state["admin"].get("litellm-blue"), 8)

    def test_the_old_color_is_stopped_only_once_its_requests_are_done(self):
        result, calls, _ = self.run_release(sessions={"blue": [2, 1, 1, 0]})
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.index(calls, self.stopped("litellm-blue"))
        polls = [c for c in calls[:stop] if "show stat" in c["stdin"]]
        self.assertGreaterEqual(len(polls), 4)

    def test_streams_a_reloaded_proxy_still_carries_hold_the_old_color(self):
        # After a configuration reload the previous proxy worker keeps the
        # streams it had, out of the new worker's counts.
        result, calls, _ = self.run_release(workers=[3, 3, 3, 2])
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.index(calls, self.stopped("litellm-blue"))
        counted = [c for c in calls[:stop] if "pgrep" in c["args"]]
        self.assertGreaterEqual(len(counted), 4)

    def test_requests_still_open_at_the_drain_cap_do_not_hold_the_release_forever(self):
        result, calls, state = self.run_release(
            sessions={"blue": [1]}, GATEWAY_DRAIN_SECONDS="0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("litellm-blue", state["running"])
        self.assertIn("1 open requests", result.stdout)

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

    def test_a_changed_proxy_is_replaced_only_with_acknowledged_interruption(self):
        result, calls, state = self.run_release(RUNNING_PROXY_HASH="old")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])

    def test_moving_from_the_single_container_needs_acknowledged_interruption(self):
        result, calls, state = self.run_release(serving=(), legacy=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state["legacy"])
        self.assertFalse(any(c["args"][0] == "compose" and "up" in c["args"] for c in calls))

    def test_moving_from_the_single_container_stops_it_only_once_blue_is_ready(self):
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        blue = self.index(calls, self.started("litellm-blue"))
        stop = self.index(calls, lambda a, s: a[0] == "stop" and a[-1] == LEGACY)
        proxy = self.index(calls, lambda a, s: self.started("gateway-proxy")(a, s) and "--no-start" not in a)
        self.assertLess(blue, stop)
        self.assertLess(stop, proxy)
        self.assertFalse(state["legacy"])
        self.assertEqual(state["running"], ["gateway-proxy", "litellm-blue"])

    def test_a_proxy_that_does_not_come_up_gives_the_single_container_back(self):
        result, calls, state = self.run_release(
            serving=(), legacy=True, GATEWAY_ALLOW_INTERRUPT="1", FAIL_UP="gateway-proxy"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state["legacy"])
        self.assertTrue(any(c["args"] == ["start", LEGACY] for c in calls))
        self.assertEqual(state["running"], [])


if __name__ == "__main__":
    unittest.main()
