"""The public execution route must not hold a DB connection over RPC."""

import unittest
from pathlib import Path

ROUTE = Path(__file__).parents[2] / "app/api/routes/execution.py"


class ExecutionRoutePoolContract(unittest.TestCase):
    def test_remote_call_is_not_wrapped_in_a_transaction_advisory_lock(self):
        source = ROUTE.read_text(encoding="utf-8")

        self.assertNotIn(
            "await execution.lock_release(db, resource_id, shared=True)", source
        )
        self.assertIn("business route must release its database connection", source)


if __name__ == "__main__":
    unittest.main()
