#!/usr/bin/env python3
"""Set backend/alembic/HEAD to this checkout's only Alembic head.

From the repository root, using the existing backend dependencies:
    uv run --project backend python .claude/scripts/reanchor-migration-head.py
    uv run --project backend python .claude/scripts/reanchor-migration-head.py --dry-run
    uv run --offline --no-sync --project backend python \
        .claude/scripts/reanchor-migration-head.py --self-test

Reads migration modules as `alembic heads` does; never runs env.py, upgrades a
DB, edits migrations, fetches main, or stages files. A fork must be rechained
first, per .claude/rules/migrations.md. This checks the working tree only;
check-migration-fork.py still owns the comparison against origin/main.
Exit 0: updated, already correct, or valid dry-run. Exit 1: refused, no write.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def reanchor(root: Path, *, dry_run: bool = False) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = root / "backend"
    sentinel = backend / "alembic/HEAD"
    config = Config(str(backend / "alembic.ini"))
    # Anchor the repository's prepend paths instead of depending on the caller's cwd.
    config.set_main_option("path_separator", "os")
    config.set_main_option(
        "prepend_sys_path", os.pathsep.join(map(str, (backend, backend / "alembic")))
    )
    with warnings.catch_warnings():
        # Duplicate revision IDs and missing parents are warnings in Alembic.
        # A writer must refuse those graphs rather than choose a plausible tip.
        warnings.filterwarnings("error", category=UserWarning, module=r"alembic\..*")
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
        if len(heads) != 1:
            raise ValueError(
                f"expected exactly one Alembic head, found {len(heads)}: "
                f"{', '.join(sorted(heads)) or '(none)'}. "
                "Rechain your migration first; HEAD was not changed."
            )
        tip = heads[0]

    desired = f"{tip}\n".encode()
    try:
        current = sentinel.read_bytes()
    except FileNotFoundError:
        current = None
    if current == desired:
        return f"UNCHANGED: backend/alembic/HEAD already names the only head ({tip})"

    old = (
        repr(current.decode("utf-8", errors="replace").strip())
        if current is not None
        else "<missing>"
    )
    if dry_run:
        return f"DRY-RUN: backend/alembic/HEAD would change from {old} to {tip} (one head verified)"

    # Replace only after validation and a complete write, leaving the old file
    # intact if writing fails. The temporary lives beside HEAD for atomic rename.
    with tempfile.NamedTemporaryFile(dir=sentinel.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(desired)
            handle.flush()
            os.chmod(temporary, sentinel.stat().st_mode if sentinel.exists() else 0o644)
            handle.close()
            os.replace(temporary, sentinel)
        finally:
            temporary.unlink(missing_ok=True)
    return f"UPDATED: backend/alembic/HEAD {old} -> {tip} (one head verified)"


class ReanchorTests(unittest.TestCase):
    """Offline CLI tests: real Alembic graphs, no database or network."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.versions = self.root / "backend/alembic/versions"
        self.versions.mkdir(parents=True)
        (self.root / "backend/alembic.ini").write_text(
            "[alembic]\nscript_location = %(here)s/alembic\n", encoding="utf-8"
        )
        self.sentinel = self.versions.parent / "HEAD"
        self.sentinel.write_bytes(b"old\n")
        self.migration("a", None)

    def migration(self, revision: str, parent: str | tuple[str, ...] | None) -> None:
        (self.versions / f"{revision}.py").write_text(
            f"revision: str = {revision!r}\ndown_revision = {parent!r}\n",
            encoding="utf-8",
        )

    def run_cli(self, *args: str, success: bool = True) -> str:
        before = self.sentinel.read_bytes() if self.sentinel.exists() else None
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--root",
                str(self.root),
                *args,
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode, 0 if success else 1, result.stdout + result.stderr
        )
        if not success:
            self.assertEqual(self.sentinel.read_bytes(), before)
        return result.stdout + result.stderr

    def test_single_head_repairs_stale_sentinel(self) -> None:
        self.migration("b", "a")
        self.assertIn("UPDATED:", self.run_cli())
        self.assertEqual(self.sentinel.read_bytes(), b"b\n")

    def test_two_heads_leave_sentinel_untouched(self) -> None:
        self.migration("b", "a")
        self.migration("c", "a")
        self.assertIn("found 2", self.run_cli(success=False))
        self.run_cli("--dry-run", success=False)

    def test_correct_sentinel_is_not_rewritten(self) -> None:
        self.sentinel.write_bytes(b"a\n")
        os.utime(self.sentinel, ns=(1234567890000000000, 1234567890000000000))
        before = self.sentinel.stat()
        self.assertIn("UNCHANGED:", self.run_cli())
        after = self.sentinel.stat()
        self.assertEqual(
            (after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns)
        )
        self.assertEqual(self.sentinel.read_bytes(), b"a\n")

    def test_dry_run_does_not_write(self) -> None:
        self.assertIn("DRY-RUN:", self.run_cli("--dry-run"))
        self.assertEqual(self.sentinel.read_bytes(), b"old\n")
        self.sentinel.unlink()
        self.run_cli("--dry-run")
        self.assertFalse(self.sentinel.exists())

    def test_missing_sentinel_is_created(self) -> None:
        self.sentinel.unlink()
        self.run_cli()
        self.assertEqual(self.sentinel.read_bytes(), b"a\n")

    def test_merge_revision_is_the_only_head(self) -> None:
        self.migration("b", "a")
        self.migration("c", "a")
        self.migration("m", ("b", "c"))
        self.run_cli()
        self.assertEqual(self.sentinel.read_bytes(), b"m\n")

    def test_empty_graph_is_rejected(self) -> None:
        (self.versions / "a.py").unlink()
        self.assertIn("found 0", self.run_cli(success=False))

    def test_missing_parent_is_rejected(self) -> None:
        self.migration("b", "missing")
        self.run_cli(success=False)

    def test_duplicate_revision_is_rejected(self) -> None:
        self.migration("b", "a")
        (self.versions / "duplicate.py").write_bytes(
            (self.versions / "a.py").read_bytes()
        )
        self.run_cli(success=False)

    def test_invalid_migration_is_rejected(self) -> None:
        (self.versions / "broken.py").write_text("<<<<<<< HEAD\n", encoding="utf-8")
        self.run_cli(success=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="checkout root (default: this script's checkout)",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="validate and report, without writing"
    )
    modes.add_argument(
        "--self-test", action="store_true", help="test offline in temporary directories"
    )
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ReanchorTests)
        )
        return 0 if result.wasSuccessful() else 1
    try:
        print(reanchor(args.root.resolve(), dry_run=args.dry_run))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
