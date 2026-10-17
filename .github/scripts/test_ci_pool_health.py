"""Two of three machines were down for hours and the check said the pool was up."""

import contextlib
import importlib.util
import io
import os
import pathlib
import unittest
import urllib.error
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pool", _HERE / "ci-pool-health.py")
pool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pool)


def runner(name: str, box: str, status: str = "online", busy: bool = False) -> dict:
    labels = [{"name": "self-hosted"}, {"name": "cheese-ci"}]
    if box:
        labels.append({"name": f"cheese-ci-box-{box}"})
    return {"name": name, "status": status, "busy": busy, "labels": labels}


class PoolHealth(unittest.TestCase):
    def test_a_full_pool_is_quiet(self) -> None:
        healthy, problems = pool.assess(
            [runner("r1", "1"), runner("r1b", "1"), runner("r2", "2"), runner("r2b", "2")]
        )
        self.assertEqual(problems, [])
        self.assertEqual(len(healthy), 2)

    def test_busy_is_working_not_missing(self) -> None:
        """The whole pool is busy during a merge burst. A check that read that as
        death is the reason the old one only ever asked whether ONE machine
        answered."""
        _healthy, problems = pool.assess([runner("r1", "1", busy=True)])
        self.assertEqual(problems, [])

    def test_a_machine_that_is_gone_is_named(self) -> None:
        """This is the case that went unseen for hours: the other machines were
        answering, so the shared-label check kept passing."""
        _healthy, problems = pool.assess(
            [runner("r1", "1"), runner("r2", "2", status="offline"), runner("r3", "3")]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("cheese-ci-box-2", problems[0])
        self.assertIn("offline", problems[0])

    def test_half_a_machine_is_already_a_problem(self) -> None:
        """Losing one slot is lost capacity, and it is the state that precedes
        losing the other one."""
        _healthy, problems = pool.assess(
            [runner("r1", "1"), runner("r1b", "1", status="offline")]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("r1b=offline", problems[0])

    def test_an_empty_pool_is_a_problem_not_a_clean_run(self) -> None:
        """Nothing registered reads as nothing wrong to anything that counts
        failures."""
        _healthy, problems = pool.assess([])
        self.assertEqual(len(problems), 1)
        self.assertIn("no runner", problems[0])

    def test_other_pools_are_not_this_check_s_business(self) -> None:
        dev = {"name": "cheese-dev-env1", "status": "offline", "busy": False,
               "labels": [{"name": "cheese-dev"}]}
        _healthy, problems = pool.assess([runner("r1", "1"), dev])
        self.assertEqual(problems, [])

    def _main_when_the_api_fails(self, error: BaseException) -> int:
        """Run main([]) with credentials present and the runner API failing.

        The credentials are what make these tests bite: without them main
        returns 2 before it ever asks the API, and a check that reported every
        API failure as healthy would still pass.
        """
        fetch = mock.Mock(side_effect=error)
        with (
            mock.patch.dict(os.environ, {"REPO": "owner/repo", "GH_TOKEN": "token"}),
            mock.patch.object(pool, "fetch_runners", fetch),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = pool.main([])
        fetch.assert_called_once()
        return code

    def test_a_forbidden_runner_list_is_not_a_healthy_pool(self) -> None:
        """The API refusing to answer is the check failing to see the pool. It
        is neither a healthy pool nor a missing machine, so it must not exit 0
        (reported fine) or 1 (a machine is down)."""
        code = self._main_when_the_api_fails(
            urllib.error.HTTPError(
                "https://api.github.com/repos/owner/repo/actions/runners",
                403,
                "Forbidden",
                None,
                None,
            )
        )
        self.assertEqual(code, 2)

    def test_an_unreachable_runner_api_is_not_a_healthy_pool(self) -> None:
        code = self._main_when_the_api_fails(
            urllib.error.URLError(OSError("connection refused"))
        )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
