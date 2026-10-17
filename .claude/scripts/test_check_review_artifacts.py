#!/usr/bin/env python3
"""Self-test the review-artifact guard: a fake orphan must be caught."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-review-artifacts.py")


class ReviewArtifactGuardTests(unittest.TestCase):
    def test_unreferenced_artifact_is_rejected(self) -> None:
        root = self._tree({"artifacts/orphan.pdf": b"%PDF-1.4", "note.md": "# 评审\n"})
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("orphan.pdf", result.stderr)

    def test_artifact_linked_from_a_note_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts/kept.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bare_filename_mention_passes(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "验收看 kept.pdf 这一份。\n"}
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_hyphenated_longer_name_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "[另一份](artifacts/trial-review.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("review.pdf", result.stderr)

    def test_a_dot_separated_longer_name_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "[旧版](artifacts/2026.09.review.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_chinese_longer_name_is_not_a_citation(self) -> None:
        # `验收报告.pdf` must not satisfy the orphan `报告.pdf`.
        root = self._tree(
            {
                "artifacts/报告.pdf": b"%PDF-1.4",
                "note.md": "[验收](artifacts/验收报告.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_longer_extension_is_not_a_citation(self) -> None:
        # `review.pdf.bak` must not satisfy `review.pdf`.
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "备份在 review.pdf.bak。\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_the_same_basename_in_another_directory_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/detail.pdf": b"%PDF-1.4",
                "note.md": "[细节](artifacts/run-42/detail.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_artifact_in_a_subdirectory_is_matched_by_full_path(self) -> None:
        root = self._tree(
            {
                "artifacts/run-42/detail.pdf": b"%PDF-1.4",
                "note.md": "[细节](artifacts/run-42/detail.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_percent_encoded_link_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "[预览](artifacts/2026%20review.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_sentence_period_is_not_part_of_the_name(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "See artifacts/kept.pdf.\n"}
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_citation_in_a_nested_note_counts(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "2026-09-28-review/note.md": "[产物](artifacts/kept.pdf)\n",
            }
        )
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_document_inside_the_artifacts_directory_does_not_count(self) -> None:
        root = self._tree(
            {
                "artifacts/orphan.pdf": b"%PDF-1.4",
                "artifacts/README.md": "orphan.pdf\n",
            }
        )
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_missing_reviews_directory_cannot_be_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("无法判断", result.stderr)

    def test_real_repository_passes_through_the_default_root(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def _tree(self, files: dict[str, bytes | str]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        reviews = root / "docs" / "reviews"
        reviews.mkdir(parents=True)
        for name, content in files.items():
            path = reviews / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return root

    @staticmethod
    def _run(root: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(SCRIPT)]
        if root is not None:
            command += ["--root", str(root)]
        return subprocess.run(command, check=False, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
