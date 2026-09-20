"""Public forge configuration is derived without loading or exposing secrets."""

import os
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


if __name__ == "__main__":
    unittest.main()
