"""Test immutable image rollout, CI gates and legacy rollback without a daemon."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release-metering-proxy.sh"
DIGEST = "ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:" + "b" * 64
FAKE = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
home = pathlib.Path(os.environ["METERING_PROXY_HOME"])
record = {
    "args": args,
    "image": os.environ.get("METERING_PROXY_IMAGE"),
    "config": os.environ.get("METERING_PROXY_CONFIG_SHA256"),
}
if args[0] == "compose":
    files = [args[i+1] for i, arg in enumerate(args) if arg == "-f"]
    record["configs"] = [pathlib.Path(f).read_text() for f in files]
    record["saved_image"] = list(home.glob("releases/*/previous-image"))[0].read_text().strip()
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(record) + "\n")
if args[:2] == ["network", "inspect"] and os.environ.get("NO_NETWORK"):
    sys.exit(1)
if args[:2] == ["image", "inspect"]:
    print(os.environ.get("FAKE_DIGEST", "ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:" + "b"*64))
elif args[0] == "inspect":
    field = args[-1]
    # The label the previous release left on the running container.
    if "config-sha256" in field: print(os.environ.get("FAKE_CONFIG", ""))
    elif "config_files" in field: print(os.environ["COMPOSE_FILES"])
    elif "working_dir" in field: print(home)
    elif "compose.project" in field: print(os.environ.get("FAKE_PROJECT", "metering-proxy"))
    elif ".Config.Image" in field: print(os.environ.get("FAKE_CURRENT_IMAGE", "mitmproxy/mitmproxy:12.1.2"))
    elif ".State.Running" in field: print(os.environ.get("FAKE_RUNNING", "true") + " " + os.environ.get("FAKE_HEALTH", "healthy"))
    else: print("sha256:previous")
elif args[0] == "compose" and os.environ.get("FAIL_RELEASE") and record["image"] != "sha256:previous":
    sys.exit(1)
"""


ENV_TEXT = "METERING_PROXY_IMAGE=old-env-image\nTEST_SECRET=private-test-value\n"


class MeteringReleaseTest(unittest.TestCase):
    def run_release(self, multiple=False, env_text=ENV_TEXT, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".env").write_text(env_text)
            old = home / "compose.yml"
            old.write_text(
                "services:\n  metering-proxy:\n    image: mitmproxy/mitmproxy:12.1.2\n    volumes:\n      - .:/addons:ro\n      - ./certs:/home/mitmproxy/.mitmproxy\n      - /ledger:/var/log/cheese\n"
            )
            files = [old]
            if multiple:
                extra = home / "rollback.yml"
                extra.write_text(
                    'services:\n  metering-proxy:\n    image: "sha256:previous"\n'
                )
                files.append(extra)
            docker = home / "docker"
            docker.write_text(FAKE)
            docker.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{home}:{os.environ['PATH']}",
                METERING_PROXY_HOME=folder,
                GITHUB_ACTIONS="true",
                METERING_ALLOW_INTERRUPT="1",
                GITHUB_RUN_ID="123",
                GITHUB_RUN_ATTEMPT="1",
                CALLS=str(home / "calls"),
                COMPOSE_FILES=",".join(map(str, files)),
                METERING_PROXY_IMAGE="stale-runner-image",
            )
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(SCRIPT), "a" * 40], env=env, capture_output=True, text=True
            )
            calls = (
                [json.loads(line) for line in (home / "calls").read_text().splitlines()]
                if (home / "calls").exists()
                else []
            )
            self.installed_login = all(
                (home / name).is_file() and os.access(home / name, os.X_OK)
                for name in ("claude-login.sh", "chatgpt-login.sh")
            )
            self.assertNotIn("private-test-value", result.stdout + result.stderr)
            # Nothing the release keeps or labels the container with holds a
            # secret from the .env.
            for kept in (home / "releases").rglob("*"):
                if kept.is_file():
                    self.assertNotIn("private-test-value", kept.read_text(), kept)
            for call in calls:
                self.assertNotIn("private-test-value", call["config"] or "")
            self.assertEqual((home / ".env").read_text(), env_text)
            for call in calls:
                if call["args"][0] == "compose":
                    self.assertEqual(
                        call["args"][call["args"].index("--project-directory") + 1],
                        folder,
                    )
                    self.assertEqual(
                        call["args"][call["args"].index("--env-file") + 1],
                        str(home / ".env"),
                    )
            return result, calls

    def test_compose_uses_exported_digest_and_preserves_project_relative_mounts(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            env_file = home / ".env"
            env_file.write_text("METERING_PROXY_IMAGE=stale-env-image\n")
            env = dict(
                os.environ, METERING_PROXY_IMAGE=DIGEST, METERING_PROXY_CONFIG_SHA256="c" * 64
            )
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    folder,
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(ROOT / "deploy/metering-proxy/compose.yml"),
                    "config",
                    "--format",
                    "json",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            service = json.loads(result.stdout)["services"]["metering-proxy"]
            self.assertEqual(service["image"], DIGEST)
            self.assertEqual(
                service["labels"]["cheese.metering-proxy.config-sha256"], "c" * 64
            )
            mounts = {m["target"]: m["source"] for m in service["volumes"]}
            self.assertNotIn("/addons", mounts)
            self.assertEqual(mounts["/var/log/cheese"], str(home / "logs"))
            self.assertEqual(
                mounts["/etc/cheese/claude-credential"], str(home / "claude-credential")
            )
            self.assertEqual(
                mounts["/etc/cheese/chatgpt-credential"],
                str(home / "chatgpt-credential"),
            )
            self.assertEqual(mounts["/home/mitmproxy/.mitmproxy"], str(home / "certs"))
            # The ChatGPT listener is published nowhere; the gateway reaches it
            # on the private network, by the alias it puts in api_base.
            published = [p["target"] for p in service["ports"]]
            self.assertEqual(sorted(published), [8444])
            self.assertEqual(
                service["networks"]["meter-gateway"]["aliases"], ["metering-proxy"]
            )
            self.assertIn("default", service["networks"])
            network = json.loads(result.stdout)["networks"]["meter-gateway"]
            self.assertEqual(network["name"], "cheese-meter-gateway")
            self.assertTrue(network["external"])

    def test_requires_explicit_interruption_acknowledgement(self):
        result, calls = self.run_release(METERING_ALLOW_INTERRUPT="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_releases_digest_not_tag_after_saving_old_image(self):
        result, calls = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls[0]["args"],
            ["pull", "ghcr.io/sageseekersociety/cheese/metering-proxy:" + "a" * 40],
        )
        release = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(release), 1)
        self.assertEqual(release[0]["image"], DIGEST)
        self.assertEqual(release[0]["saved_image"], "sha256:previous")
        self.assertNotIn(".:/addons:ro", release[0]["configs"][0])
        self.assertEqual(
            release[0]["args"][-8:],
            [
                "up",
                "-d",
                "--force-recreate",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "150",
                "metering-proxy",
            ],
        )

    def released_config(self, env_text=ENV_TEXT):
        """The configuration label a release puts on the container it starts
        from this .env."""
        result, calls = self.run_release(env_text=env_text)
        self.assertEqual(result.returncode, 0, result.stderr)
        label = next(c["config"] for c in calls if c["args"][0] == "compose")
        self.assertTrue(label)
        return label

    def test_healthy_same_digest_and_configuration_preserves_running_streams(self):
        result, calls = self.run_release(
            FAKE_CURRENT_IMAGE=DIGEST, FAKE_CONFIG=self.released_config()
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no restart needed", result.stdout)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))
        # The login tool still arrives when the image does not change.
        self.assertTrue(self.installed_login)

    def test_a_changed_env_is_applied_though_the_digest_is_unchanged(self):
        before = self.released_config()
        changed = ENV_TEXT + "CHEESE_TOKEN_CAP=5000\n"
        result, calls = self.run_release(
            env_text=changed, FAKE_CURRENT_IMAGE=DIGEST, FAKE_CONFIG=before
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("no restart needed", result.stdout)
        release = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(release), 1)
        self.assertIn("--force-recreate", release[0]["args"])
        self.assertEqual(release[0]["image"], DIGEST)
        # The recreated container carries the new configuration's label, so the
        # release after it leaves it running.
        self.assertEqual(release[0]["config"], self.released_config(changed))
        self.assertNotEqual(release[0]["config"], before)

    def test_a_container_no_release_labelled_is_recreated(self):
        result, calls = self.run_release(FAKE_CURRENT_IMAGE=DIGEST, FAKE_CONFIG="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(c["args"][0] == "compose" for c in calls), 1)

    def test_a_changed_digest_is_recreated_though_the_configuration_is_not(self):
        result, calls = self.run_release(FAKE_CONFIG=self.released_config())
        self.assertEqual(result.returncode, 0, result.stderr)
        release = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(release), 1)
        self.assertEqual(release[0]["image"], DIGEST)
        self.assertIn("--force-recreate", release[0]["args"])

    def test_a_release_installs_the_login_tool_beside_the_credential(self):
        result, _calls = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.installed_login)

    def test_a_release_creates_the_gateway_network_when_it_is_missing(self):
        for missing in ("", "1"):
            result, calls = self.run_release(NO_NETWORK=missing)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = [c["args"] for c in calls]
            created = ["network", "create", "--internal", "cheese-meter-gateway"]
            compose = next(i for i, a in enumerate(args) if a[0] == "compose")
            if missing:
                self.assertLess(args.index(created), compose)
            else:
                self.assertNotIn(created, args)

    def test_unhealthy_same_digest_is_recreated(self):
        result, calls = self.run_release(
            FAKE_CURRENT_IMAGE=DIGEST, FAKE_HEALTH="unhealthy"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(c["args"][0] == "compose" for c in calls), 1)
        release = next(c for c in calls if c["args"][0] == "compose")
        self.assertIn("--force-recreate", release["args"])

    def test_stopped_same_digest_is_not_mistaken_for_healthy_service(self):
        result, calls = self.run_release(
            FAKE_CURRENT_IMAGE=DIGEST, FAKE_RUNNING="false"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(c["args"][0] == "compose" for c in calls), 1)

    def test_missing_digest_never_recreates_service(self):
        result, calls = self.run_release(FAKE_DIGEST="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))

    def test_foreign_project_never_recreates_service(self):
        result, calls = self.run_release(FAKE_PROJECT="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))

    def test_failed_rollout_restores_legacy_mounts_and_checks_listeners(self):
        result, calls = self.run_release(FAIL_RELEASE="1")
        self.assertNotEqual(result.returncode, 0)
        release = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(release), 2)
        self.assertEqual(release[-1]["image"], "sha256:previous")
        self.assertIn(".:/addons:ro", release[-1]["configs"][0])
        self.assertIn("./certs:", release[-1]["configs"][0])
        self.assertIn("/ledger:", release[-1]["configs"][0])
        self.assertIn('image: "sha256:previous"', release[-1]["configs"][-1])
        # The restored container does not run the configuration that failed, so
        # it must not carry that configuration's label: the next release with
        # the same image and .env has to try again rather than exit early.
        rollback = yaml.safe_load(release[-1]["configs"][-1])["services"]
        self.assertEqual(
            rollback["metering-proxy"]["labels"],
            {"cheese.metering-proxy.config-sha256": ""},
        )
        self.assertEqual(
            calls[-1]["args"], ["exec", "-i", "cheese-metering-proxy", "python", "-"]
        )

    def test_release_after_previous_rollback_preserves_all_raw_configs(self):
        result, calls = self.run_release(multiple=True, FAIL_RELEASE="1")
        self.assertNotEqual(result.returncode, 0)
        release = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(release), 2)
        self.assertEqual(len(release[-1]["configs"]), 3)
        self.assertIn(".:/addons:ro", release[-1]["configs"][0])
        self.assertIn('image: "sha256:previous"', release[-1]["configs"][1])


class MeteringWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-metering-proxy.yml").read_text()
        )

    def test_only_main_explicit_interruption_runs_on_dev(self):
        job = self.workflow["jobs"]["release"]
        self.assertEqual(
            job["if"], "github.ref == 'refs/heads/main' && inputs.interrupt"
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
        self.assertEqual(step["env"]["METERING_ALLOW_INTERRUPT"], "1")
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
