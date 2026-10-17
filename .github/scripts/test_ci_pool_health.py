"""Two of three machines were down for hours and the check said the pool was up."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
_WORKFLOW = _HERE.parent / "workflows" / "box-uptime.yml"
_spec = importlib.util.spec_from_file_location("pool", _HERE / "ci-pool-health.py")
pool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pool)


def _outputs(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    return values


def runner(name: str, box: str, status: str = "online", busy: bool = False) -> dict:
    labels = [{"name": "self-hosted"}, {"name": "cheese-ci"}]
    if box:
        labels.append({"name": f"cheese-ci-box-{box}"})
    return {"name": name, "status": status, "busy": busy, "labels": labels}


class PoolHealth(unittest.TestCase):
    def test_a_full_pool_is_quiet(self) -> None:
        healthy, problems = pool.assess(
            [
                runner("r1", "1"),
                runner("r1b", "1"),
                runner("r2", "2"),
                runner("r2b", "2"),
            ]
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
        dev = {
            "name": "cheese-dev-env1",
            "status": "offline",
            "busy": False,
            "labels": [{"name": "cheese-dev"}],
        }
        _healthy, problems = pool.assess([runner("r1", "1"), dev])
        self.assertEqual(problems, [])

    def _run_main(
        self,
        runners: list[dict] | None = None,
        error: BaseException | None = None,
    ) -> tuple[int, dict[str, str]]:
        """Run main([]) with credentials and a real GITHUB_OUTPUT file.

        The credentials are what make these tests bite: without them main
        returns 2 before it ever asks the API, and a check that reported every
        API failure as healthy would still pass. The output file is the
        workflow's whole input, so what it says is the alert the team gets.
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = pathlib.Path(directory.name) / "output"
        output.touch()
        fetch = (
            mock.Mock(side_effect=error)
            if error is not None
            else mock.Mock(return_value=runners)
        )
        with (
            mock.patch.dict(
                os.environ,
                {
                    "REPO": "owner/repo",
                    "GH_TOKEN": "token",
                    "GITHUB_OUTPUT": str(output),
                },
            ),
            mock.patch.object(pool, "fetch_runners", fetch),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = pool.main([])
        fetch.assert_called_once()
        return code, _outputs(output)

    def _run_main_against_response(
        self, body: bytes | BaseException
    ) -> tuple[int, dict[str, str]]:
        """Run main([]) through the real fetch path against one canned body.

        Injection at `fetch_runners` alone never executes the response-shape
        checks, so a 200 whose body is JSON but not a runner list would still
        go unread here.
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = pathlib.Path(directory.name) / "output"
        output.touch()

        response = mock.MagicMock()
        response.__enter__.return_value = response
        if isinstance(body, BaseException):
            response.read.side_effect = body
        else:
            response.read.return_value = body

        with (
            mock.patch.dict(
                os.environ,
                {
                    "REPO": "owner/repo",
                    "GH_TOKEN": "token",
                    "GITHUB_OUTPUT": str(output),
                },
            ),
            mock.patch.object(
                pool.urllib.request, "urlopen", mock.Mock(return_value=response)
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = pool.main([])
        return code, _outputs(output)

    def test_a_healthy_pool_exits_zero_and_sends_no_alert(self) -> None:
        """The third of the three dispatch answers, through main(): a healthy
        pool must not page anyone. It guards the dispatch rather than the bug
        this change fixes, which is why it is green before the change too."""
        code, outputs = self._run_main(runners=[runner("r1", "1")])
        self.assertEqual(code, 0)
        self.assertNotIn("alert", outputs)

    def test_a_missing_machine_says_a_machine_is_down(self) -> None:
        """The one failure that really is a machine lost has to say so, and
        name it, so the reader is not left to open the run log."""
        code, outputs = self._run_main(
            runners=[runner("r1", "1"), runner("r2", "2", status="offline")]
        )
        self.assertEqual(code, 1)
        self.assertIn("CI 机器掉了", outputs["alert"])
        self.assertIn("cheese-ci-box-2", outputs["alert"])

    def test_an_uninterpretable_runner_list_does_not_claim_it_was_unreadable(
        self,
    ) -> None:
        """The list arrived; reading it failed. The alert has to say which, or
        the reader goes looking at an API that actually answered."""
        code, outputs = self._run_main(
            runners=[{"name": "r1", "status": "online", "labels": [{"id": 1}]}]
        )
        self.assertEqual(code, 2)
        self.assertIn("读到了", outputs["alert"])
        self.assertNotIn("读不到", outputs["alert"])
        self.assertNotIn("CI 机器掉了", outputs["alert"])

    def test_a_forbidden_runner_list_says_it_could_not_judge(self) -> None:
        """The API refusing to answer is the check failing to see the pool. It
        is neither a healthy pool nor a missing machine, so it must not exit 0
        (reported fine) or 1 (a machine is down), and its alert must not read
        as the machine-down one."""
        code, outputs = self._run_main(
            error=urllib.error.HTTPError(
                "https://api.github.com/repos/owner/repo/actions/runners",
                403,
                "Forbidden",
                None,
                None,
            )
        )
        self.assertEqual(code, 2)
        self.assertIn("读不到", outputs["alert"])
        self.assertIn("HTTP 403", outputs["alert"])
        self.assertNotIn("CI 机器掉了", outputs["alert"])

    def test_an_unreachable_runner_api_is_not_a_healthy_pool(self) -> None:
        code, outputs = self._run_main(
            error=urllib.error.URLError(OSError("connection refused"))
        )
        self.assertEqual(code, 2)
        self.assertIn("读不到", outputs["alert"])

    def test_a_malformed_runner_list_says_it_could_not_judge(self) -> None:
        """Bad JSON raises out of json, and an uncaught exception exits 1 — the
        machine-down answer. The alert then names a lost machine that is
        probably fine."""
        code, outputs = self._run_main(
            error=json.JSONDecodeError("Expecting value", "", 0)
        )
        self.assertEqual(code, 2)
        self.assertIn("读不到", outputs["alert"])
        self.assertNotIn("CI 机器掉了", outputs["alert"])

    def test_a_200_without_a_runners_list_says_it_could_not_judge(self) -> None:
        """A 200 carrying some other JSON object is not an empty pool: reading
        it as one reports a lost machine the response never mentioned."""
        code, outputs = self._run_main_against_response(
            b'{"message": "API rate limit exceeded"}'
        )
        self.assertEqual(code, 2)
        self.assertIn("读不到", outputs["alert"])
        self.assertNotIn("CI 机器掉了", outputs["alert"])

    def test_a_truncated_body_says_it_could_not_judge(self) -> None:
        """A connection dropped mid-body raises IncompleteRead, which is
        neither an OSError nor a ValueError. Uncaught it exits 1 and writes no
        alert, and the workflow only speaks when there is one."""
        code, outputs = self._run_main_against_response(
            http.client.IncompleteRead(b"", 99974)
        )
        self.assertEqual(code, 2)
        self.assertIn("读不到", outputs["alert"])
        self.assertNotIn("CI 机器掉了", outputs["alert"])

    def test_missing_credentials_still_send_an_alert(self) -> None:
        """The other exit-2 branch must speak too, or the workflow's
        non-empty-alert gate turns it into silence."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = pathlib.Path(directory.name) / "output"
        output.touch()
        with (
            mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = pool.main([])
        self.assertEqual(code, 2)
        self.assertIn("读不到", _outputs(output)["alert"])

    def test_the_workflow_sends_the_alert_the_script_decided(self) -> None:
        """The workflow's job is to send the script's sentence. A fallback of
        its own, such as "见运行日志", is how both failures used to arrive as
        the same message."""
        text = _WORKFLOW.read_text(encoding="utf-8")
        # The step has to be addressable as `pool`, which is what the alert
        # step reads its sentence from.
        self.assertIn("id: pool", text)
        self.assertIn("always()", text)
        self.assertIn("steps.pool.outputs.alert != ''", text)
        self.assertIn("ALERT: ${{ steps.pool.outputs.alert }}", text)
        self.assertIn('"$ALERT"', text)
        self.assertNotIn("见运行日志", text)
        self.assertNotIn("steps.pool.outputs.problems", text)


if __name__ == "__main__":
    unittest.main()
