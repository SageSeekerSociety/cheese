"""Verify forge addresses, private event credentials, and release health checks."""

import os
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap-forgejo.py"


class ForgeConfigTest(unittest.TestCase):
    def prepare(self, content, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "backend.env"
            env_file.write_text(content)
            environment = os.environ.copy()
            for key in ("FORGEJO_URL", "FORGEJO_WEBHOOK_HOSTS", "FORGE_WEBHOOK_URL"):
                environment.pop(key, None)
            environment.update(overrides)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--prepare",
                    "--backend-env",
                    str(env_file),
                ],
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(env_file.read_text(), content)
            return result

    def exports(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(
            shlex.split(line)[1].split("=", 1) for line in result.stdout.splitlines()
        )

    def test_existing_site_supplies_default_forge_address(self):
        result = self.prepare(
            'FRONTEND_URL="https://cheese.example/"\nFORGEJO_URL=\nFORGE_WEBHOOK_URL=\n'
            'JWT_SECRET="private value never printed"\n'
        )
        self.assertEqual(
            self.exports(result),
            {
                "FORGEJO_URL": "https://cheese.example/forge/",
                "FORGEJO_WEBHOOK_HOSTS": "cheese.example",
            },
        )
        self.assertNotIn("private value", result.stdout + result.stderr)

    def test_public_relay_hostname_is_allowed(self):
        result = self.prepare(
            "FRONTEND_URL=https://cheese.example\n"
            "FORGE_WEBHOOK_URL=https://relay.example/api/forge/events/deployment\n"
        )
        self.assertEqual(self.exports(result)["FORGEJO_WEBHOOK_HOSTS"], "relay.example")

    def test_deployment_overrides_take_precedence(self):
        result = self.prepare(
            "FORGEJO_URL=https://old.example/forge/\n",
            FORGEJO_URL="https://forge.example",
            FORGEJO_WEBHOOK_HOSTS="relay.example,internal.example",
        )
        self.assertEqual(
            self.exports(result),
            {
                "FORGEJO_URL": "https://forge.example/",
                "FORGEJO_WEBHOOK_HOSTS": "relay.example,internal.example",
            },
        )

    def test_missing_public_address_fails_before_provisioning(self):
        result = self.prepare("JWT_SECRET=unused\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_credentials_and_query_strings_cannot_become_public_addresses(self):
        for url in (
            "https://user:secret@forge.example/",
            "https://forge.example/?token=secret",
            "file:///data",
        ):
            with self.subTest(url=url):
                result = self.prepare("", FORGEJO_URL=url)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(url, result.stderr)


class EventConfigTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.backend = Path(self.directory.name) / "backend.env"
        self.relay = Path(self.directory.name) / "relay.env"

    def configure(self):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--configure-events",
                "--backend-env",
                str(self.backend),
                "--relay-env",
                str(self.relay),
            ],
            capture_output=True,
            text=True,
        )

    def values(self, path):
        return dict(line.split("=", 1) for line in path.read_text().splitlines())

    def test_first_release_connects_to_local_relay_and_reuses_credentials(self):
        original = "FRONTEND_URL=https://cheese.example\nJWT_SECRET=backend-only\n"
        self.backend.write_text(original)
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        backend, relay = self.values(self.backend), self.values(self.relay)
        deployment = relay["FORGE_EVENT_LOCAL_DEPLOYMENT"]
        secret = backend["FORGE_EVENT_SECRET"]
        self.assertEqual(
            json.loads(relay["FORGE_EVENT_RELAY_KEYS"]), {deployment: secret}
        )
        self.assertEqual(
            backend["FORGE_WEBHOOK_URL"],
            f"https://cheese.example/api/forge/events/{deployment}",
        )
        self.assertEqual(
            backend["FORGE_EVENT_RELAY_URL"],
            f"wss://cheese.example/api/forge/events/{deployment}/connect",
        )
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("backend-only", self.relay.read_text())
        for path in (self.backend, self.relay):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            next(self.backend.parent.glob("backend.env.*.bak")).read_text(), original
        )
        saved = self.backend.read_text(), self.relay.read_text()
        self.assertEqual(self.configure().returncode, 0)
        self.assertEqual(saved, (self.backend.read_text(), self.relay.read_text()))

    def test_external_relay_remains_external(self):
        original = (
            "FORGE_EVENT_RELAY_URL=wss://relay.example/forge/events/private/connect\n"
            "FORGE_WEBHOOK_URL=https://relay.example/forge/events/private\n"
            "FORGE_EVENT_SECRET=existing-secret\n"
        )
        self.backend.write_text(original)
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FORGE_EVENTS_LOCAL=false", result.stdout)
        self.assertEqual(self.backend.read_text(), original)
        self.assertFalse(self.relay.exists())

    def test_missing_local_credentials_cannot_silently_disable_the_relay(self):
        self.backend.write_text("FRONTEND_URL=https://cheese.example\n")
        self.assertEqual(self.configure().returncode, 0)
        original = self.backend.read_text()
        self.relay.unlink()
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.backend.read_text(), original)

    def test_interrupted_setup_preserves_other_deployments_and_shared_app(self):
        self.backend.write_text("FRONTEND_URL=https://cheese.example\n")
        self.relay.write_text(
            "FORGE_EVENT_LOCAL_DEPLOYMENT=local\n"
            'FORGE_EVENT_RELAY_KEYS={"local":"saved-secret","other":"other-secret"}\n'
            "FORGE_EVENT_GITHUB_SECRET=app-secret\n"
            'FORGE_EVENT_GITHUB_INSTALLATIONS={"11":["other"]}\n'
        )
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.values(self.backend)["FORGE_EVENT_SECRET"], "saved-secret"
        )
        relay = self.values(self.relay)
        self.assertEqual(
            json.loads(relay["FORGE_EVENT_RELAY_KEYS"])["other"], "other-secret"
        )
        self.assertEqual(relay["FORGE_EVENT_GITHUB_SECRET"], "app-secret")
        self.assertEqual(
            json.loads(relay["FORGE_EVENT_GITHUB_INSTALLATIONS"]), {"11": ["other"]}
        )

    def test_partial_configuration_fails_without_replacing_it(self):
        original = "FRONTEND_URL=https://cheese.example\nFORGE_WEBHOOK_URL=https://relay.example/events\n"
        self.backend.write_text(original)
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.backend.read_text(), original)
        self.assertFalse(self.relay.exists())


class EventHealthTest(unittest.TestCase):
    def test_local_relay_must_be_present_healthy_and_on_this_release(self):
        script = SCRIPT.parent / "check-app-tier.sh"
        base = (
            "backend\trepo/backend:release\trunning\tUp (healthy)\n"
            "frontend\trepo/frontend:release\trunning\tUp (healthy)\n"
            "forgejo\tforgejo:15\trunning\tUp (healthy)\n"
        )
        for row, expected in (
            ("", 1),
            ("forge-events\trepo/backend:old\trunning\tUp (healthy)\n", 1),
            ("forge-events\trepo/backend:release\trunning\tUp (unhealthy)\n", 1),
            ("forge-events\trepo/backend:release\trunning\tUp (healthy)\n", 0),
        ):
            with self.subTest(row=row):
                result = subprocess.run(
                    ["bash", str(script), "release"],
                    input=base + row,
                    env={**os.environ, "FORGE_EVENTS_LOCAL": "true"},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
