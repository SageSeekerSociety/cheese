#!/usr/bin/env python3
"""Self-test the review-artifact guard: a fake orphan must be caught."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".claude" / "scripts" / "check-review-artifacts.py"
ORPHAN = 1
CANNOT_JUDGE = 2


class ReviewArtifactGuardTests(unittest.TestCase):
    # --- the orphan verdict -------------------------------------------------

    def test_unreferenced_artifact_is_rejected(self) -> None:
        root = self._tree({"artifacts/orphan.pdf": b"%PDF-1.4", "note.md": "# 评审\n"})
        result = self._run(root)
        self.assertEqual(result.returncode, ORPHAN, result.stderr)
        self.assertIn("orphan.pdf", result.stderr)

    def test_a_hyphenated_longer_name_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "[另一份](artifacts/trial-review.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_dot_separated_longer_name_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "[旧版](artifacts/2026.09.review.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_chinese_longer_name_is_not_a_citation(self) -> None:
        # `验收报告.pdf` must not satisfy the orphan `报告.pdf`.
        root = self._tree(
            {
                "artifacts/报告.pdf": b"%PDF-1.4",
                "note.md": "[验收](artifacts/验收报告.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_longer_extension_is_not_a_citation(self) -> None:
        # `review.pdf.bak` must not satisfy `review.pdf`.
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "备份在 review.pdf.bak。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_longer_word_is_not_a_citation(self) -> None:
        root = self._tree(
            {"artifacts/review.pdf": b"%PDF-1.4", "note.md": "看 review.pdfs。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_the_same_basename_in_another_directory_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/detail.pdf": b"%PDF-1.4",
                "note.md": "[细节](artifacts/run-42/detail.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_different_space_joined_path_is_not_a_citation(self) -> None:
        # `review.pdf` is the tail of `old/2026 review.pdf`, a different path.
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "[另一份](old/2026%20review.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_document_inside_the_artifacts_directory_does_not_count(self) -> None:
        # README.md is cited from outside, so it is not itself an orphan; if it
        # were treated as a document, its mention of orphan.pdf would pass.
        root = self._tree(
            {
                "artifacts/orphan.pdf": b"%PDF-1.4",
                "artifacts/README.md": "orphan.pdf\n",
                "note.md": "[说明](artifacts/README.md)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    # --- citations that must pass ------------------------------------------

    def test_artifact_linked_from_a_note_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts/kept.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_bare_filename_mention_passes(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "验收看 kept.pdf 这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_path_glued_to_chinese_prose_passes(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "详见artifacts/kept.pdf这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_artifact_in_a_subdirectory_is_matched_by_full_path(self) -> None:
        root = self._tree(
            {
                "artifacts/run-42/detail.pdf": b"%PDF-1.4",
                "note.md": "[细节](artifacts/run-42/detail.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_percent_encoded_link_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "[预览](artifacts/2026%20review.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_literal_space_in_an_angled_link_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "[预览](<artifacts/2026 review.pdf>)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_literal_parenthesis_in_an_angled_link_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/report(final).pdf": b"%PDF-1.4",
                "note.md": "[报告](<artifacts/report(final).pdf>)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_literal_plus_in_a_path_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/v1+fix.pdf": b"%PDF-1.4",
                "note.md": "[修复](<artifacts/v1+fix.pdf>)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_literal_percent_in_a_name_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/discount%20off.pdf": b"%PDF-1.4",
                "note.md": "[价格](artifacts/discount%20off.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_sentence_period_is_not_part_of_the_name(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "See artifacts/kept.pdf.\n"}
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_citation_in_a_nested_note_counts(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "2026-09-28-review/note.md": "[产物](artifacts/kept.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_link_title_is_ignored(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": '[产物](artifacts/kept.pdf "预览")\n',
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_title_after_an_angled_link_is_ignored(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": '[预览](<artifacts/2026 review.pdf> "预览")\n',
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_fragment_is_ignored(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts/kept.pdf#page=2)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_query_is_ignored(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts/kept.pdf?raw=1)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_percent_encoded_citation_in_prose_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/2026%20review.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_percent_encoded_prefix_does_not_satisfy_the_tail(self) -> None:
        # Decoding the prose must not turn `2026%20review.pdf` into a separate
        # citation of `review.pdf`.
        root = self._tree(
            {
                "artifacts/review.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/2026%20review.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_trailing_path_segment_is_not_a_citation(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/kept.pdf/x 这里。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_percent_sign_does_not_delimit_a_bare_name(self) -> None:
        root = self._tree(
            {"artifacts/fix.pdf": b"%PDF-1.4", "note.md": "折扣 100%fix.pdf 已生效。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_bare_link_destination_does_not_count(self) -> None:
        # `[x](kept.pdf)` resolves beside the note, not inside artifacts/.
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "[产物](kept.pdf)\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_backslash_separated_prose_path_is_not_a_citation(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "见 old\\kept.pdf 这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_bare_name_glued_to_chinese_is_not_a_citation(self) -> None:
        # Ambiguous with a longer Chinese filename: write the artifacts/ path.
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "详见kept.pdf这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_path_prefix_is_not_a_citation(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "见 myartifacts/kept.pdf 这里。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_parent_relative_path_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](../artifacts/kept.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_repository_relative_path_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](docs/reviews/artifacts/kept.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_windows_separator_in_a_link_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts\\kept.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_two_links_on_one_line_both_count(self) -> None:
        root = self._tree(
            {
                "artifacts/a.pdf": b"%PDF-1.4",
                "artifacts/b.pdf": b"%PDF-1.4",
                "note.md": "[甲](artifacts/a.pdf) 和 [乙](artifacts/b.pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_an_angled_link_with_a_space_and_parentheses_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/report (final).pdf": b"%PDF-1.4",
                "note.md": '[报告](<artifacts/report (final).pdf> "预览")\n',
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_truncated_angled_link_does_not_name_a_shorter_artifact(self) -> None:
        root = self._tree(
            {
                "artifacts/2026": b"%PDF-1.4",
                "note.md": "[另一份](<artifacts/2026 review.pdf>)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_percent_encoded_path_before_a_sentence_period_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "see artifacts/2026%20review.pdf.\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_percent_encoded_path_beside_literal_chinese_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/报告 v2.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/报告%20v2.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_plain_link_with_balanced_parentheses_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/report(final).pdf": b"%PDF-1.4",
                "note.md": "[报告](artifacts/report(final).pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_escaped_parentheses_in_a_link_pass(self) -> None:
        root = self._tree(
            {
                "artifacts/report(final).pdf": b"%PDF-1.4",
                "note.md": "[报告](artifacts/report\\(final\\).pdf)\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_percent_encoded_plus_in_prose_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review+final.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/2026%20review+final.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_percent_encoded_parentheses_in_prose_pass(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review (final).pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/2026%20review%20(final).pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_mix_of_chinese_and_an_encoded_space_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/报告 2026.pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/报告%202026.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_fully_encoded_bare_name_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/报告.pdf": b"%PDF-1.4",
                "note.md": "见 %E6%8A%A5%E5%91%8A.pdf. Next.\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_reference_definition_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物][kept]\n\n[kept]: artifacts/kept.pdf\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_reference_definition_with_a_bare_name_does_not_count(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物][kept]\n\n[kept]: kept.pdf\n",
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_an_html_bare_href_does_not_count(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": '<a href="kept.pdf">产物</a>\n',
            }
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_an_html_canonical_href_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": '<a href="artifacts/kept.pdf">产物</a>\n',
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_link_with_a_trailing_ellipsis_does_not_count(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "[产物](artifacts/kept.pdf...)\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_plus_joined_longer_name_is_not_a_citation(self) -> None:
        root = self._tree(
            {"artifacts/fix.pdf": b"%PDF-1.4", "note.md": "看 v1+fix.pdf 这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_title_on_the_next_line_still_hides_a_bare_destination(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": '[x](kept.pdf\n"t")\n'}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_title_on_the_next_line_after_an_angled_destination(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": '[x](<kept.pdf>\n"t")\n'}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_partially_encoded_bare_name_with_a_prefix_passes(self) -> None:
        root = self._tree(
            {
                "artifacts/2026 review.pdf": b"%PDF-1.4",
                "note.md": "见 2026%20review.pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_full_width_percent_does_not_delimit_a_bare_name(self) -> None:
        root = self._tree(
            {"artifacts/fix.pdf": b"%PDF-1.4", "note.md": "折扣 100％fix.pdf 生效。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_a_full_width_slash_does_not_delimit_a_bare_name(self) -> None:
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "见 kept.pdf／x 这一份。\n"}
        )
        self.assertEqual(self._run(root).returncode, ORPHAN)

    def test_escaped_parentheses_in_prose_pass(self) -> None:
        root = self._tree(
            {
                "artifacts/report(final).pdf": b"%PDF-1.4",
                "note.md": "见 artifacts/report\\(final\\).pdf 这一份。\n",
            }
        )
        self.assertEqual(self._run(root).returncode, 0)

    def test_a_broken_symlink_note_cannot_be_judged(self) -> None:
        root = self._tree({"artifacts/kept.pdf": b"%PDF-1.4"})
        link = root / "docs" / "reviews" / "note.md"
        link.symlink_to(root / "docs" / "reviews" / "missing.md")
        result = self._run(root)
        self.assertEqual(result.returncode, CANNOT_JUDGE, result.stderr)

    def test_an_unreadable_subdirectory_cannot_be_judged(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can read a mode-000 directory")
        root = self._tree(
            {
                "artifacts/kept.pdf": b"%PDF-1.4",
                "note.md": "[产物](artifacts/kept.pdf)\n",
                "sub/other.md": "nothing\n",
            }
        )
        sub = root / "docs" / "reviews" / "sub"
        sub.chmod(0)
        self.addCleanup(sub.chmod, 0o755)
        result = self._run(root)
        self.assertEqual(result.returncode, CANNOT_JUDGE, result.stderr)

    def test_an_unreadable_artifacts_directory_cannot_be_judged(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can read a mode-000 directory")
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "[产物](artifacts/kept.pdf)\n"}
        )
        artifacts = root / "docs" / "reviews" / "artifacts"
        artifacts.chmod(0)
        self.addCleanup(artifacts.chmod, 0o755)
        result = self._run(root)
        self.assertEqual(result.returncode, CANNOT_JUDGE, result.stderr)

    # --- cannot judge -------------------------------------------------------

    def test_a_missing_reviews_directory_cannot_be_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory))
        self.assertEqual(result.returncode, CANNOT_JUDGE, result.stderr)
        self.assertIn("无法判断", result.stderr)

    def test_an_unreadable_note_cannot_be_judged(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can read a mode-000 file")
        root = self._tree(
            {"artifacts/kept.pdf": b"%PDF-1.4", "note.md": "[产物](artifacts/kept.pdf)\n"}
        )
        note = root / "docs" / "reviews" / "note.md"
        note.chmod(0)
        self.addCleanup(note.chmod, 0o644)
        result = self._run(root)
        self.assertEqual(result.returncode, CANNOT_JUDGE, result.stderr)
        self.assertIn("无法判断", result.stderr)

    def test_real_repository_passes_through_the_default_root(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- helpers ------------------------------------------------------------

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
