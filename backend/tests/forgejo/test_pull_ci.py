"""Run the forge fixture against isolated commands; never use a Docker daemon."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTTP_503 = (
    " snapshots Pulling \n"
    " forgejo Pulling \n"
    " 44bb90d0dc2d Pulling fs layer \n"
    " 5492f30fd58e Downloading [==================================================>]"
    "     503B/503B\n"
    " forgejo Error received unexpected HTTP status: 503 Service Unavailable\n"
    "Error response from daemon: received unexpected HTTP status:"
    " 503 Service Unavailable\n"
)


def shell_path(path):
    value = path.as_posix()
    if os.name == "nt":
        return f"/{value[0].lower()}{value[2:]}"
    return value


class PullCITest(unittest.TestCase):
    def run_start(
        self,
        outcomes=(),
        *,
        overrides=None,
        missing=None,
        signal=None,
        signal_stage="pull",
        real_timeout=False,
        ignore_term=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            commands = case / "bin"
            commands.mkdir()
            (case / "token").write_text("existing-fixture-token\n")
            for attempt, (status, output) in enumerate(outcomes, 1):
                (case / f"status-{attempt}").write_text(str(status))
                (case / f"output-{attempt}").write_text(output)
            scripts = {
                "docker": r"""#!/usr/bin/env bash
printf 'docker' >> "$CALLS"
printf '\t%s' "$@" >> "$CALLS"
printf '\n' >> "$CALLS"
for arg in "$@"; do
  if [[ "$arg" == pull ]]; then
    if [[ "$FAKE_REAL_TIMEOUT" == 1 && -n "$FAKE_SIGNAL" ]]; then
      printf '%s' "$$" > "$CASE_DIR/client-pid"
      if [[ "$FAKE_IGNORE_TERM" == 1 ]]; then
        trap '' TERM
      else
        trap '
          kill -TERM "$blocker"
          wait "$blocker"
          printf "lifecycle\tdocker\tterminated\t143\n" >> "$CALLS"
          exit 143
        ' TERM
      fi
      /usr/bin/sleep 8 &
      blocker=$!
      "$TEST_PYTHON" -c 'import time; print(time.monotonic())' > "$CASE_DIR/signal-sent"
      kill -s "$FAKE_SIGNAL" "$(cat "$CASE_DIR/helper-pid")"
      wait "$blocker"
      printf 'lifecycle\tdocker\tcompleted\n' >> "$CALLS"
      exit 0
    fi
    count=0
    if [[ -f "$CASE_DIR/count" ]]; then count=$(cat "$CASE_DIR/count"); fi
    count=$((count + 1))
    printf '%s' "$count" > "$CASE_DIR/count"
    cat "$CASE_DIR/output-$count"
    exit "$(cat "$CASE_DIR/status-$count")"
  fi
done
exit "${FAKE_UP_EXIT:-0}"
""",
                "timeout": r"""#!/usr/bin/env bash
if [[ "$1" == --version ]]; then
  if [[ "$FAKE_REAL_TIMEOUT" == 1 ]]; then exec "$REAL_TIMEOUT" "$@"; fi
  printf 'timeout (GNU coreutils) 9.5\n'
  exit 0
fi
printf 'timeout' >> "$CALLS"
printf '\t%s' "$@" >> "$CALLS"
printf '\n' >> "$CALLS"
if [[ "$FAKE_REAL_TIMEOUT" == 1 ]]; then
  printf '%s' "$PPID" > "$CASE_DIR/helper-pid"
  exec "$REAL_TIMEOUT" "$@"
fi
if [[ -n "$FAKE_SIGNAL" && "$FAKE_SIGNAL_STAGE" == pull ]]; then
  printf '%s' "$$" > "$CASE_DIR/client-pid"
  trap '
    printf "lifecycle\ttimeout\tterminated\t143\n" >> "$CALLS"
    exit 143
  ' TERM
  "$TEST_PYTHON" -c 'import time; print(time.monotonic())' > "$CASE_DIR/signal-sent"
  kill -s "$FAKE_SIGNAL" "$PPID"
  # Keep the command alive after signalling its parent. Any foreground child
  # finishes within 1s, so the substitute can handle TERM and reap it promptly.
  for tick in {1..8}; do /usr/bin/sleep 1; done
  printf 'lifecycle\ttimeout\tcompleted\n' >> "$CALLS"
  exit 0
fi
shift 2
"$@"
""",
                "sleep": r"""#!/usr/bin/env bash
printf 'sleep\t%s\n' "$*" >> "$CALLS"
if [[ -n "$FAKE_SIGNAL" && "$FAKE_SIGNAL_STAGE" == backoff ]]; then
  printf '%s' "$$" > "$CASE_DIR/client-pid"
  trap '
    printf "lifecycle\tsleep\tterminated\t143\n" >> "$CALLS"
    exit 143
  ' TERM
  "$TEST_PYTHON" -c 'import time; print(time.monotonic())' > "$CASE_DIR/signal-sent"
  kill -s "$FAKE_SIGNAL" "$PPID"
  for tick in {1..8}; do /usr/bin/sleep 1; done
  printf 'lifecycle\tsleep\tcompleted\n' >> "$CALLS"
fi
""",
                "curl": "#!/usr/bin/env bash\nexit 0\n",
            }
            for name, content in scripts.items():
                command = commands / name
                command.write_text(content, newline="\n")
                command.chmod(0o755)
            if missing:
                # Restrict PATH to a tool set without the real command as a
                # fallback; all installed commands stay untouched.
                (commands / missing).unlink()
            env = os.environ.copy()
            for key in (
                "GITHUB_ENV",
                "FORGEJO_TEST_CI_PULL_RETRY",
                "GITHUB_ACTIONS",
                "RUNNER_OS",
                "DOCKER_CONTEXT",
            ):
                env.pop(key, None)
            env.update(
                CASE_DIR=shell_path(case),
                CALLS=shell_path(case / "calls"),
                TEST_BIN=shell_path(commands),
                TEST_PYTHON=shell_path(Path(sys.executable)),
                START_SCRIPT=shell_path(HERE / "start.sh"),
                FORGEJO_TEST_TOKEN_FILE=shell_path(case / "token"),
                FORGEJO_TEST_PROJECT="isolated-pull-test",
                FORGEJO_TEST_CI_PULL_RETRY="1",
                GITHUB_ACTIONS="true",
                RUNNER_OS="Linux",
                DOCKER_CONTEXT="default",
                FAKE_SIGNAL=signal or "",
                FAKE_SIGNAL_STAGE=signal_stage,
                FAKE_REAL_TIMEOUT="1" if real_timeout else "0",
                FAKE_IGNORE_TERM="1" if ignore_term else "0",
            )
            for key, value in (overrides or {}).items():
                if value is None:
                    env.pop(key, None)
                else:
                    env[key] = value
            shell = os.environ.get("FORGEJO_PULL_TEST_BASH", "bash")
            setup = (
                'export REAL_TIMEOUT="$(command -v timeout)"; '
                'export PATH="$TEST_BIN:$PATH"; exec bash "$START_SCRIPT"'
            )
            if missing:
                # Resolve the remaining tools before narrowing PATH. All
                # wrappers are test-local. Unlike copied MSYS executables,
                # wrappers also find their DLLs with this restricted PATH.
                setup = (
                    'real_bash="$(command -v bash)"; '
                    "for tool in bash dirname seq cat rm mktemp awk sed sleep; do "
                    'if [[ "$tool" != "$MISSING_TOOL" '
                    '&& ! -f "$TEST_BIN/$tool" ]]; then '
                    'printf \'#!%s\nexec "%s" "$@"\n\' '
                    '"$real_bash" "$(command -v "$tool")" > "$TEST_BIN/$tool"; '
                    'chmod +x "$TEST_BIN/$tool"; '
                    'fi; done; export PATH="$TEST_BIN"; '
                    'exec bash "$START_SCRIPT"'
                )
                env["MISSING_TOOL"] = missing
            argv = [shell, "-c", setup]
            started = time.monotonic()
            try:
                result = subprocess.run(
                    argv,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except subprocess.TimeoutExpired as exc:
                observed = (
                    (case / "calls").read_text()
                    if (case / "calls").exists()
                    else "no command boundary reached"
                )
                self.fail(
                    f"fixture command timed out: {argv!r}\n"
                    f"observed calls:\n{observed}\n"
                    f"stdout: {exc.stdout!r}\nstderr: {exc.stderr!r}"
                )
            calls = []
            if (case / "calls").exists():
                calls = [
                    line.split("\t")
                    for line in (case / "calls").read_text().splitlines()
                ]
            if signal:
                signalled = (
                    float((case / "signal-sent").read_text())
                    if (case / "signal-sent").exists()
                    else started
                )
                calls.append(["elapsed", str(time.monotonic() - signalled)])
            if (case / "client-pid").exists():
                client_pid = (case / "client-pid").read_text()
                probe = subprocess.run(
                    [shell, "-c", 'kill -0 "$1" 2>/dev/null', "probe", client_pid],
                    capture_output=True,
                    timeout=5,
                )
                calls.append(
                    ["owned-client", "running" if probe.returncode == 0 else "exited"]
                )
            return result, calls

    def docker_actions(self, calls, action):
        return [
            call[call.index(action) :]
            for call in calls
            if call[0] == "docker" and action in call
        ]

    def assert_pull_count(self, calls, count):
        self.assertEqual(
            self.docker_actions(calls, "pull"),
            [["pull", "--policy", "missing"]] * count,
            calls,
        )
        bounded = [call for call in calls if call[0] == "timeout"]
        self.assertEqual(len(bounded), count, calls)
        for call in bounded:
            self.assertEqual(call[1:3], ["--kill-after=5s", "120s"])

    def assert_no_start(self, calls):
        self.assertEqual(self.docker_actions(calls, "up"), [], calls)

    def assert_prompt_cancel(self, calls, grace=0):
        elapsed = float(next(c[1] for c in calls if c[0] == "elapsed"))
        # Measure response after sending the signal, excluding fixture setup.
        # The controlled child stays alive for 8s without cancellation; GNU
        # timeout has 5s KILL grace when used.
        self.assertLess(elapsed, 3 + grace, calls)

    def test_first_pull_success_starts_once_without_another_pull(self):
        result, calls = self.run_start([(0, " forgejo Pulled\n")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_pull_count(calls, 1)
        self.assertEqual(
            self.docker_actions(calls, "up"), [["up", "-d", "--pull", "never"]]
        )
        self.assertEqual([c for c in calls if c[0] == "sleep"], [])

    def test_complete_503_diagnostics_retry_then_start(self):
        for output in (HTTP_503, f"\x1b[31m{HTTP_503}\x1b[0m"):
            with self.subTest(output=output):
                result, calls = self.run_start([(1, output), (0, "")])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_pull_count(calls, 2)
                self.assertEqual(
                    [c for c in calls if c[0] == "sleep"], [["sleep", "10"]]
                )
                self.assertEqual(
                    self.docker_actions(calls, "up"),
                    [["up", "-d", "--pull", "never"]],
                )
                self.assertIn(output, result.stdout + result.stderr)

    def test_persistent_503_stops_after_three_attempts(self):
        result, calls = self.run_start([(1, HTTP_503)] * 3)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_pull_count(calls, 3)
        self.assertEqual(
            [c for c in calls if c[0] == "sleep"], [["sleep", "10"], ["sleep", "20"]]
        )
        self.assert_no_start(calls)

    def test_other_failures_preserve_the_exit_code_without_retry(self):
        outputs = [
            (1, HTTP_503.replace("503", "404")),
            (1, "Error response from daemon: unauthorized: authentication required\n"),
            (1, "Error response from daemon: denied: requested access is denied\n"),
            (1, "Error response from daemon: manifest unknown\n"),
            (7, HTTP_503),
            (1, "connection refused\n"),
        ]
        for status, output in outputs:
            with self.subTest(status=status, output=output):
                result, calls = self.run_start([(status, output)])
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_pull_count(calls, 1)
                self.assert_no_start(calls)
                self.assertEqual([c for c in calls if c[0] == "sleep"], [])

    def test_mixed_unknown_and_truncated_diagnostics_do_not_retry(self):
        for output in (
            HTTP_503 + " snapshots Error unauthorized: authentication required\n",
            HTTP_503 + "Error response from daemon: received unexpected HTTP status:"
            " 429 Too Many Requests\n",
            HTTP_503 + "Error: unexpected registry response\n",
            HTTP_503 + "daemon disconnected\n",
            HTTP_503 + " forgejo Error received unexpected HTTP status: 503\n",
            "HTTP 503 Service Unavailable\n",
            " forgejo Pulling\n",
            "",
        ):
            with self.subTest(output=output):
                result, calls = self.run_start([(1, output), (0, "")])
                self.assert_pull_count(calls, 1)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assert_no_start(calls)

    def test_timeout_kill_and_signal_exit_codes_never_retry(self):
        for status in (124, 137, 130, 143):
            with self.subTest(status=status):
                result, calls = self.run_start([(status, HTTP_503)])
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_pull_count(calls, 1)
                self.assert_no_start(calls)

    def test_interrupt_and_termination_stop_before_start(self):
        for name, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=name):
                result, calls = self.run_start(signal=name)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(len([c for c in calls if c[0] == "timeout"]), 1)
                self.assert_prompt_cancel(calls)
                self.assertIn(["lifecycle", "timeout", "terminated", "143"], calls)
                self.assertIn(["owned-client", "exited"], calls)
                self.assert_no_start(calls)

    def test_backoff_interrupt_reaps_sleep_without_another_pull(self):
        for name, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=name):
                result, calls = self.run_start(
                    [(1, HTTP_503)], signal=name, signal_stage="backoff"
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_pull_count(calls, 1)
                self.assert_prompt_cancel(calls)
                self.assertIn(["lifecycle", "sleep", "terminated", "143"], calls)
                self.assertIn(["owned-client", "exited"], calls)
                self.assertEqual(
                    [c for c in calls if c[0] == "sleep"], [["sleep", "10"]]
                )
                self.assert_no_start(calls)

    def test_real_timeout_reaps_a_client_that_ignores_term_within_kill_grace(self):
        for name, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=name):
                result, calls = self.run_start(
                    signal=name, real_timeout=True, ignore_term=True
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_pull_count(calls, 1)
                self.assert_prompt_cancel(calls, grace=5)
                self.assertIn(["owned-client", "exited"], calls)
                self.assertNotIn(["lifecycle", "docker", "completed"], calls)
                self.assert_no_start(calls)

    def test_cached_images_still_use_missing_policy_and_never_pull_on_start(self):
        result, calls = self.run_start([(0, "")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_pull_count(calls, 1)
        self.assertEqual(
            self.docker_actions(calls, "up"), [["up", "-d", "--pull", "never"]]
        )

    def test_start_failure_is_not_retried(self):
        result, calls = self.run_start([(0, "")], overrides={"FAKE_UP_EXIT": "9"})
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assert_pull_count(calls, 1)
        self.assertEqual(
            self.docker_actions(calls, "up"), [["up", "-d", "--pull", "never"]]
        )

    def test_ci_opt_in_requires_github_actions_on_linux(self):
        for overrides in (
            {"GITHUB_ACTIONS": None},
            {"GITHUB_ACTIONS": "false"},
            {"RUNNER_OS": None},
            {"RUNNER_OS": "Windows"},
            {"RUNNER_OS": "macOS"},
            {"FORGEJO_TEST_CI_PULL_RETRY": "yes"},
        ):
            with self.subTest(overrides=overrides):
                result, calls = self.run_start(overrides=overrides)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_missing_ci_tool_fails_without_pulling_or_starting(self):
        result, calls = self.run_start(missing="timeout")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, [])

    def test_local_default_uses_colima_without_ci_tools_or_pull_policy(self):
        result, calls = self.run_start(
            overrides={
                "FORGEJO_TEST_CI_PULL_RETRY": None,
                "GITHUB_ACTIONS": None,
                "RUNNER_OS": None,
                "DOCKER_CONTEXT": None,
            },
            missing="timeout",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.docker_actions(calls, "up"), [["up", "-d"]])
        self.assertEqual(calls[0][1:3], ["--context", "colima"])
        self.assertEqual(self.docker_actions(calls, "pull"), [])
        self.assertEqual([c for c in calls if c[0] == "timeout"], [])


if __name__ == "__main__":
    unittest.main()
