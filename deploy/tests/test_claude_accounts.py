"""A rejected account stays out of circulation until one recovery request succeeds."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "metering-proxy"))
from cheese_billing_core import PlatformCredential
from claude_accounts import ClaudeAccounts


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "credential").write_text("token-a")
        (self.root / "accounts" / "second").mkdir(parents=True)
        (self.root / "accounts" / "second" / "credential").write_text("token-b")
        self.clock = 1000
        self.pool = ClaudeAccounts(
            PlatformCredential(self.root / "credential"), now=lambda: self.clock
        )

    def test_new_sessions_share_accounts_and_existing_sessions_stick(self):
        self.assertEqual(self.pool.select("one")[0], "primary")
        self.assertEqual(self.pool.select("two")[0], "second")
        self.assertEqual(
            [self.pool.select("one")[0] for _ in range(10)], ["primary"] * 10
        )

    def test_429_switch_stays_switched_and_survives_restart(self):
        self.pool.select("one")
        self.pool.reject("primary", {"retry-after": "100"}, b"rate limit")
        self.assertEqual(self.pool.select("one", excluded=("primary",))[0], "second")
        self.assertEqual(
            [self.pool.select("one")[0] for _ in range(10)], ["second"] * 10
        )
        restarted = ClaudeAccounts(
            PlatformCredential(self.root / "credential"), now=lambda: self.clock
        )
        self.assertEqual(restarted.select("one")[0], "second")

    def test_only_one_recovery_request_and_failed_probe_backs_off(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "10"}, b"rate limit")
        self.clock += 11
        self.assertEqual(self.pool.select("probe")[0], "primary")
        self.assertEqual(self.pool.select("concurrent")[0], "second")
        self.pool.reject("primary", {}, b"rate limit")
        self.assertEqual(self.pool.select("probe")[0], "second")
        self.clock += 601
        self.assertEqual(self.pool.select("recovery")[0], "primary")
        self.pool.accepted("primary")
        self.assertNotIn("primary", self.pool.state)

    def test_quota_without_reset_requires_operator_and_does_not_flap(self):
        self.pool.accounts()
        self.pool.reject("primary", {}, b"weekly limit reached")
        self.clock += 86400 * 10
        self.assertEqual(self.pool.select("one")[0], "second")
        self.pool.reject("second", {}, b"weekly limit reached")
        self.assertIsNone(self.pool.select("one"))
        self.assertIsNone(self.pool.retry_after())

    def test_abandoned_probe_is_not_immediately_retried(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "10"}, b"rate limit")
        self.clock += 11
        self.pool.select("probe")
        self.pool.release("primary")
        self.assertEqual(self.pool.select("another")[0], "second")

    def test_recovery_does_not_move_an_existing_healthy_session(self):
        self.pool.select("one")
        self.pool.reject("primary", {"retry-after": "10"}, b"rate limit")
        self.assertEqual(self.pool.select("one")[0], "second")
        self.clock += 11
        self.assertEqual(self.pool.select("one")[0], "second")
        self.assertEqual(self.pool.select("new")[0], "primary")

    def test_old_response_cannot_complete_another_requests_probe(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "10"}, b"rate limit")
        self.clock += 11
        self.pool.select("probe", request_id="new")
        self.pool.accepted("primary", "old")
        self.assertEqual(self.pool.select("concurrent")[0], "second")
        self.pool.accepted("primary", "new")
        self.assertNotIn("primary", self.pool.state)


class SnapshotTest(unittest.TestCase):
    """The published copy of the pool — everything the admin board can see of a
    box it has no route to. Written next to the ledger, so it is read by the
    backend; a board that showed nothing while the pool was healthy would be
    worse than no board."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "credential").write_text("token-a")
        (self.root / "accounts" / "second").mkdir(parents=True)
        (self.root / "accounts" / "second" / "credential").write_text("token-b")
        self.logs = self.root / "logs"
        self.snapshot = self.logs / "accounts.json"
        self.clock = 1000
        self.pool = ClaudeAccounts(
            PlatformCredential(self.root / "credential"),
            now=lambda: self.clock,
            snapshot_path=self.snapshot,
        )

    def document(self):
        return json.loads(self.snapshot.read_text())

    def rows(self):
        return {row["name"]: row for row in self.document()["accounts"]}

    def test_a_healthy_pool_publishes_every_account(self):
        self.pool.snapshot()
        self.assertEqual(self.document()["written_at"], 1000)
        self.assertIsNone(self.document()["retry_after"])
        self.assertEqual(
            self.rows()["primary"],
            {"name": "primary", "state": "available", "until": None, "failures": 0},
        )
        self.assertEqual(list(self.rows()), ["primary", "second"])

    def test_a_429_publishes_the_cooldown_from_the_same_state(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "600"}, b"rate limit")
        row = self.rows()["primary"]
        self.assertEqual(row["state"], "cooling")
        self.assertEqual(row["until"], 1600)
        self.assertEqual(row["failures"], 1)
        self.assertEqual(self.document()["retry_after"], 600)
        self.assertEqual(self.rows()["second"]["state"], "available")

    def test_a_spent_allowance_publishes_as_disabled_not_cooling(self):
        self.pool.accounts()
        self.pool.reject("primary", {}, b"weekly limit reached")
        row = self.rows()["primary"]
        self.assertEqual(row["state"], "disabled")
        self.assertIsNone(row["until"])
        self.assertIsNone(self.document()["retry_after"])

    def test_recovery_clears_the_published_row(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "10"}, b"rate limit")
        self.clock += 11
        self.pool.select("probe", request_id="r")
        self.pool.accepted("primary", "r")
        self.assertEqual(self.rows()["primary"]["state"], "available")

    def test_republishing_recounts_the_countdown(self):
        self.pool.accounts()
        self.pool.reject("primary", {"retry-after": "600"}, b"rate limit")
        self.clock += 300
        self.pool.snapshot()
        self.assertEqual(self.document()["retry_after"], 300)
        self.assertEqual(self.document()["written_at"], 1300)

    def test_the_write_is_atomic_and_leaves_no_temporary(self):
        self.pool.snapshot()
        self.assertEqual([p.name for p in self.logs.iterdir()], ["accounts.json"])

    def test_no_destination_means_no_file_and_no_error(self):
        self.pool.snapshot_path = None
        self.pool.snapshot()
        self.assertFalse(self.snapshot.exists())

    def test_a_failed_write_is_logged_not_raised(self):
        # A file where the destination's directory would have to go: publishing
        # is a report, and failing to write it must not cost a model call.
        self.pool.snapshot_path = self.root / "credential" / "accounts.json"
        self.pool.snapshot()
        self.assertFalse(self.snapshot.exists())


if __name__ == "__main__":
    unittest.main()
