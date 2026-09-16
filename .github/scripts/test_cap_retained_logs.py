"""The cap exists because one file, once, cost the whole repository its CI."""

import importlib.util
import pathlib
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("capper", _HERE / "cap-retained-logs.py")
capper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capper)


class CapRetainedLogs(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_small_file_is_left_exactly_as_it_was(self) -> None:
        """Almost every run is this one. It must cost nothing and change nothing."""
        path = self.root / "runner.log"
        path.write_bytes(b"all fine\n")

        self.assertIsNone(capper.cap_file(path, cap=1024))
        self.assertEqual(path.read_bytes(), b"all fine\n")

    def test_an_oversized_log_keeps_its_tail(self) -> None:
        """The end of a log is where the failure is, so that is the half to keep."""
        path = self.root / "runner.log"
        path.write_bytes(b"x" * 500 + b"THE ACTUAL ERROR\n")

        capper.cap_file(path, cap=64)

        kept = path.read_bytes()
        self.assertIn(b"THE ACTUAL ERROR", kept)
        self.assertLess(len(kept), 400)

    def test_a_capped_log_says_so_inside_itself(self) -> None:
        """Otherwise the next reader takes the tail for the whole run and
        concludes it started where the file begins."""
        path = self.root / "runner.log"
        path.write_bytes(b"y" * 5000)

        capper.cap_file(path, cap=100)

        self.assertIn(b"cap-retained-logs", path.read_bytes())
        self.assertIn(b"bytes dropped", path.read_bytes())

    def test_structured_output_is_dropped_rather_than_half_kept(self) -> None:
        """A truncated JSON file looks readable and is not, which sends whoever
        opens it looking for a parser bug instead of a size cap."""
        path = self.root / "provider-requests.json"
        path.write_bytes(b'{"requests": [' + b'0,' * 5000 + b"]}")

        capper.cap_file(path, cap=100)

        kept = path.read_bytes()
        self.assertIn(b"cap-retained-logs", kept)
        self.assertNotIn(b'{"requests"', kept)

    def test_walking_a_tree_caps_only_what_is_over(self) -> None:
        big = self.root / "a" / "runner.log"
        big.parent.mkdir(parents=True)
        big.write_bytes(b"z" * 9000)
        small = self.root / "a" / "inputs.json"
        small.write_bytes(b"{}")

        capper.main([str(self.root), "--cap-bytes", "500"])

        self.assertLess(big.stat().st_size, 900)
        self.assertEqual(small.read_bytes(), b"{}")

    def test_a_run_that_produced_nothing_is_not_an_error(self) -> None:
        """`if: always()` means this runs after a job that died early too."""
        self.assertEqual(capper.main([str(self.root / "never-made"), "--cap-bytes", "1"]), 0)


if __name__ == "__main__":
    unittest.main()
