#!/usr/bin/env python3
"""Behavioral tests for verify-brief-paths.py using throwaway git repositories."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("verify-brief-paths.py")
spec = importlib.util.spec_from_file_location("verify_brief_paths", SCRIPT)
verify = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verify)


class VerifyBriefPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "public").mkdir()
        (self.root / "接口文档").mkdir()
        (self.root / "src/existing.py").write_text("present\n", encoding="utf-8")
        (self.root / "src/overlap.py").write_text("present\n", encoding="utf-8")
        (self.root / "docs/说明.md").write_text("present\n", encoding="utf-8")
        (self.root / "docs/config.md").write_text("present\n", encoding="utf-8")
        (self.root / "docs/new-api.md").write_text("present\n", encoding="utf-8")
        (self.root / "docs/v9.9").write_text("present\n", encoding="utf-8")
        (self.root / "docs/a.ver2").write_text("present\n", encoding="utf-8")
        (self.root / "接口文档/说明.md").write_text("present\n", encoding="utf-8")
        (self.root / "public/logo.png").write_text("present\n", encoding="utf-8")
        (self.root / "public/logo.avif").write_text("present\n", encoding="utf-8")
        (self.root / "public/demo.mp4").write_text("present\n", encoding="utf-8")
        (self.root / "public/font.woff2").write_text("present\n", encoding="utf-8")
        (self.root / "public/openapi.sha256").write_text("present\n", encoding="utf-8")
        (self.root / "public/lib.a").write_text("present\n", encoding="utf-8")
        (self.root / "public/module.io").write_text("present\n", encoding="utf-8")
        (self.root / "public/module.ml").write_text("present\n", encoding="utf-8")
        (self.root / "public/module.q").write_text("present\n", encoding="utf-8")
        (self.root / "logo.avif").write_text("present\n", encoding="utf-8")
        (self.root / "文档.md").write_text("present\n", encoding="utf-8")
        (self.root / "README.md").write_text("readme\n", encoding="utf-8")
        (self.root / "后台管理布局统一-方案B.pdf").write_text("present\n", encoding="utf-8")
        (self.root / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
        root_extensions = (
            "data.parquet", "settings.cjs", "vite.config.mts", "schema.proto",
            "release.zip", "archive.gz", "backup.tar", "video.mp4", "root-font.woff2",
            "root-openapi.sha256", "image.bmp", "archive.mkv", "movie.mov",
            "runtime.wasm", "stream.webm",
        )
        for path in root_extensions:
            (self.root / path).write_text("present\n", encoding="utf-8")
        self.root_extensions = root_extensions
        self.git(
            "add", "src", "docs", "public", "接口文档", "logo.avif", "文档.md", "README.md", "Makefile",
            *root_extensions, "后台管理布局统一-方案B.pdf",
        )
        self.git("commit", "-qm", "initial")
        self.initial = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", self.initial)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def run_tool(self, brief: str, *options: str):
        brief_file = self.root / "brief.txt"
        brief_file.write_text(brief, encoding="utf-8")
        return self.run_args(["--brief", str(brief_file), *options])

    def run_args(self, args: list[str], stdin_text: str = ""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(stdin_text)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = verify.main(["--root", str(self.root), *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_bad_brief_reports_nonexistent_path(self):
        result, _, errors = self.run_tool("Inspect src/missing.py\n")
        self.assertEqual(result, 1)
        self.assertIn("path does not exist", errors)
        self.assertIn("src/missing.py", errors)

    def test_actual_and_declared_in_flight_sets_are_each_checked(self):
        for option in ("--in-flight-actual", "--in-flight-declared"):
            with self.subTest(option=option):
                result, _, errors = self.run_tool(
                    "Use src/overlap.py\n", option, "src/overlap.py"
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("src/overlap.py", errors)

    def test_chinese_adjacent_to_path_does_not_corrupt_it(self):
        result, output, errors = self.run_tool("修改src/existing.py中的函数。\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_existing_unicode_paths_are_extracted_as_whole_paths(self):
        result, output, errors = self.run_tool(
            "Update 后台管理布局统一-方案B.pdf and 修改docs/说明.md中的段落。\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 2 brief path", output)

        result, _, errors = self.run_tool(
            "Update 后台管理布局统一-方案B.pdf\n",
            "--in-flight-actual", "后台管理布局统一-方案B.pdf",
        )
        self.assertEqual(result, 1)
        self.assertIn("后台管理布局统一-方案B.pdf", errors)

    def test_missing_unicode_path_adjacent_to_prose_is_reported(self):
        result, _, errors = self.run_tool("修改docs/不存在.md中的段落。\n")
        self.assertEqual(result, 1)
        self.assertIn("docs/不存在.md", errors)
        self.assertIn("path does not exist", errors)

    def test_missing_root_unicode_path_adjacent_to_prose_is_reported(self):
        result, _, errors = self.run_tool("Update 不存在.pdf中的说明\n")
        self.assertEqual(result, 1)
        self.assertIn("不存在.pdf", errors)
        self.assertIn("path does not exist", errors)

    def test_new_unicode_path_is_extracted_as_a_whole_path(self):
        result, output, errors = self.run_tool("新建 docs/新文件.md\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_chinese_adjacent_overlap_reports_the_path(self):
        result, _, errors = self.run_tool(
            "修改src/overlap.py，注意与在飞 PR 重叠。\n",
            "--in-flight-actual", "src/overlap.py",
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("src/overlap.py", errors)

    def test_cjk_descriptor_before_ascii_path_keeps_actual_path(self):
        result, _, errors = self.run_tool(
            "更新 接口docs/api.md中的签名。\n",
            "--in-flight-actual", "docs/api.md",
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/api.md", errors)
        self.assertNotIn("接口docs/api.md", errors)

    def test_cjk_descriptor_before_unicode_path_keeps_actual_path(self):
        brief = "新增接口docs/说明.md\n"
        result, _, errors = self.run_tool(brief)
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)
        self.assertIn("docs/说明.md", errors)
        self.assertNotIn("接口docs/说明.md", errors)

        result, _, errors = self.run_tool(
            "更新 接口docs/说明.md中的段落。\n",
            "--in-flight-actual", "docs/说明.md",
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/说明.md", errors)

        result, _, errors = self.run_tool(
            "新增接口docs/new-api.md\n",
            "--in-flight-actual", "docs/new-api.md",
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/new-api.md", errors)
        self.assertNotIn("接口docs/new-api.md", errors)

    def test_explicit_paths_do_not_read_implicit_stdin(self):
        class UnexpectedInput:
            def read(self):
                raise AssertionError("stdin should not be read in explicit-path mode")

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.stdin", UnexpectedInput()),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = verify.main(["--root", str(self.root), "--brief-path", "src/existing.py"])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertIn("verified 1 brief path", stdout.getvalue())

    def test_paths_present_in_either_ref_are_accepted(self):
        (self.root / "src/origin-only.py").write_text("origin\n", encoding="utf-8")
        self.git("add", "src/origin-only.py")
        self.git("commit", "-qm", "origin-only")
        origin_commit = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", origin_commit)
        self.git("reset", "--hard", self.initial)
        result, _, errors = self.run_tool("Use src/origin-only.py\n")
        self.assertEqual(result, 0, errors)

        (self.root / "src/head-only.py").write_text("head\n", encoding="utf-8")
        self.git("add", "src/head-only.py")
        self.git("commit", "-qm", "head-only")
        self.git("update-ref", "refs/remotes/origin/main", self.initial)
        result, _, errors = self.run_tool("Use src/head-only.py\n")
        self.assertEqual(result, 0, errors)

    def test_compliant_brief_accepts_existing_and_new_paths(self):
        result, output, errors = self.run_tool(
            "Use src/existing.py; 新建 .claude/new-helper.py\n",
            "--in-flight-actual", "src/unrelated.py",
            "--in-flight-declared", "docs/unrelated.md",
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("OK:", output)
        self.assertEqual(errors, "")

    def test_new_prose_does_not_mark_a_later_path(self):
        result, output, errors = self.run_tool("A new feature uses src/existing.py.\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_new_substring_inside_another_word_is_not_a_marker(self):
        result, output, errors = self.run_tool("Please renew src/existing.py.\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_dotted_prose_is_not_treated_as_repository_paths(self):
        result, output, errors = self.run_tool(
            "Run tests e.g. on Python 3.12; contact dev@example.com; update UI; use src/existing.py.\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_missing_root_extensionless_path_is_reported(self):
        for brief in ("Update CONTRIBUTING\n", "修改 CONTRIBUTING\n"):
            with self.subTest(brief=brief):
                result, _, errors = self.run_tool(brief)
                self.assertEqual(result, 1)
                self.assertIn("CONTRIBUTING", errors)
                self.assertIn("path does not exist", errors)

    def test_root_extensionless_paths_are_extracted_and_checked(self):
        for brief in ("Update Makefile\n", "更新 接口Makefile\n"):
            with self.subTest(brief=brief):
                result, _, errors = self.run_tool(brief)
                self.assertEqual(result, 0, errors)
                result, _, errors = self.run_tool(
                    brief, "--in-flight-actual", "Makefile"
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("Makefile", errors)

    def test_root_filename_with_extension_is_not_truncated(self):
        for brief in (
            "Update README.md\n",
            "Update README.md中的引用。\n",
            "Update 接口README.md中的引用。\n",
        ):
            with self.subTest(brief=brief):
                result, output, errors = self.run_tool(brief)
                self.assertEqual(result, 0, errors)
                self.assertIn("verified 1 brief path", output)
                result, _, errors = self.run_tool(
                    brief, "--in-flight-actual", "README.md"
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("README.md", errors)
                self.assertNotIn("README\n", errors)

    def test_root_version_labels_are_not_paths(self):
        for brief in ("Update V1.2 API\n", "Update PR.123 description\n"):
            with self.subTest(brief=brief):
                result, output, errors = self.run_tool(brief)
                self.assertEqual(result, 0, errors)
                self.assertIn("verified 0 brief path", output)

    def test_dotted_prose_is_not_a_root_path(self):
        for brief in ("Update example.com configuration\n",):
            with self.subTest(brief=brief):
                result, output, errors = self.run_tool(brief)
                self.assertEqual(result, 0, errors)
                self.assertIn("verified 0 brief path", output)

    def test_root_unknown_extension_is_checked(self):
        result, output, errors = self.run_tool("更新 logo.avif中的引用。\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)
        result, _, errors = self.run_tool(
            "更新 logo.avif中的引用。\n", "--in-flight-actual", "logo.avif"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("logo.avif", errors)

    def test_root_common_extensions_are_checked(self):
        brief = "\n".join(f"Update {path}" for path in self.root_extensions)
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn(f"verified {len(self.root_extensions)} brief path", output)
        result, _, errors = self.run_tool(
            "Update data.parquet\n", "--in-flight-actual", "data.parquet"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        result, _, errors = self.run_tool("Update missing.parquet\n")
        self.assertEqual(result, 1)
        self.assertIn("path does not exist", errors)
        self.assertIn("missing.parquet", errors)

    def test_unknown_extension_is_trimmed_before_chinese_prose(self):
        for path in (
            "public/logo.png", "public/logo.avif", "public/demo.mp4",
            "public/font.woff2", "public/openapi.sha256", "public/lib.a",
            "public/module.io", "public/module.ml", "public/module.q",
        ):
            with self.subTest(path=path):
                result, output, errors = self.run_tool(f"更新 {path}中的引用。\n")
                self.assertEqual(result, 0, errors)
                self.assertIn("verified 1 brief path", output)
                result, _, errors = self.run_tool(
                    f"更新 {path}中的引用。\n",
                    "--in-flight-actual", path,
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn(path, errors)

    def test_version_segments_are_not_trimmed_as_extensions(self):
        brief = "新建 docs/v9.9说明\n"
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)
        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", "docs/v9.9说明"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/v9.9说明", errors)

    def test_version_word_suffix_is_not_trimmed(self):
        brief = "新建 docs/a.ver2说明\n"
        paths, unsafe = verify.extract_brief_paths(brief, {"docs/a.ver2"})
        self.assertEqual(paths, {"docs/a.ver2说明": True})
        self.assertEqual(unsafe, [])
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)
        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", "docs/a.ver2说明"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/a.ver2说明", errors)

    def test_version_suffix_trims_prose_after_known_path(self):
        brief = "更新 docs/a.ver2中的引用。\n"
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)
        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", "docs/a.ver2"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/a.ver2", errors)
        self.assertNotIn("中的引用", errors)

        for marker in ("新增", "新建"):
            with self.subTest(marker=marker):
                result, _, errors = self.run_tool(
                    f"{marker} docs/a.ver2中的引用。\n"
                )
                self.assertEqual(result, 1)
                self.assertIn("marked new but already exists", errors)
                self.assertIn("docs/a.ver2", errors)

    def test_version_suffix_trims_other_prose_words_after_known_path(self):
        for prose in ("里的内容", "上的说明", "下的文件", "内的内容"):
            with self.subTest(prose=prose):
                result, _, errors = self.run_tool(
                    f"更新 docs/a.ver2{prose}。\n",
                    "--in-flight-actual", "docs/a.ver2",
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("docs/a.ver2", errors)
                self.assertNotIn(prose, errors)

    def test_known_path_prefix_does_not_hide_longer_path(self):
        path = "docs/a.ver2中的说明.md"
        result, _, errors = self.run_tool(f"更新 {path}\n")
        self.assertEqual(result, 1)
        self.assertIn("path does not exist", errors)
        self.assertIn(path, errors)
        self.assertNotIn("docs/a.ver2\n", errors)

        result, output, errors = self.run_tool(f"新增 {path}\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_attached_descriptor_and_versioned_prose_compose(self):
        brief = "更新 接口docs/a.ver2中的引用。\n"
        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", "docs/a.ver2"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("docs/a.ver2", errors)
        self.assertNotIn("接口docs/a.ver2", errors)

        result, _, errors = self.run_tool("新增接口docs/a.ver2中的引用。\n")
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)
        self.assertIn("docs/a.ver2", errors)

    def test_literal_descriptor_prefixed_path_is_not_split(self):
        path = "接口文档/说明.md"
        brief = f"更新 {path}.\n"
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", path
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn(path, errors)
        self.assertNotIn(": 文档/说明.md", errors)

    def test_new_marker_extracts_untracked_extensionless_path(self):
        result, output, errors = self.run_tool("新建 Dockerfile\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_new_marker_punctuation_keeps_the_path_visible(self):
        cases = (
            ("新建文件：src/overlap.py\n", "src/overlap.py"),
            ("新建文件:docs/config.md\n", "docs/config.md"),
            ("新增接口、docs/config.md\n", "docs/config.md"),
            ("新建文件（docs/config.md）\n", "docs/config.md"),
        )
        for brief, path in cases:
            with self.subTest(brief=brief):
                result, _, errors = self.run_tool(
                    brief, "--in-flight-actual", path
                )
                self.assertEqual(result, 1)
                self.assertIn("marked new but already exists", errors)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn(path, errors)
                self.assertNotIn("文件：", errors)

    def test_new_marker_is_local_and_supports_descriptors(self):
        for brief in (
            "新增 API src/new-api.py\n",
            "新增接口 docs/new-endpoint.md\n",
            "新建文件 docs/new.md\n",
            "新增 两个文件 src/new-c.py\n",
            "新建 文件 Dockerfile\n",
            "新增了缓存层，修改 src/existing.py\n",
        ):
            with self.subTest(brief=brief):
                result, output, errors = self.run_tool(brief)
                self.assertEqual(result, 0, errors)
                self.assertIn("verified 1 brief path", output)

    def test_new_marker_descriptor_handles_root_filenames(self):
        for brief, path in (
            ("新增文件Makefile\n", "Makefile"),
            ("新建文件README.md\n", "README.md"),
        ):
            with self.subTest(path=path):
                result, _, errors = self.run_tool(
                    brief, "--in-flight-actual", path
                )
                self.assertEqual(result, 1)
                self.assertIn("marked new but already exists", errors)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn(path, errors)

    def test_root_chinese_descriptor_is_not_split_from_filename(self):
        result, output, errors = self.run_tool("新增接口文档.md\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_counted_new_file_list_marks_both_paths(self):
        result, output, errors = self.run_tool(
            "新增 2 个文件 src/new-a.py, src/new-b.py\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 2 brief path", output)

    def test_chinese_counted_new_file_list_marks_both_paths(self):
        result, output, errors = self.run_tool(
            "新增 两个文件 src/new-a.py、src/new-b.py\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 2 brief path", output)

    def test_counted_new_file_list_rejects_existing_item(self):
        result, _, errors = self.run_tool(
            "新增 2 个文件 src/new-a.py, src/existing.py\n"
        )
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)
        self.assertIn("src/existing.py", errors)

    def test_counted_list_checks_root_level_items_after_cjk_separators(self):
        result, _, errors = self.run_tool(
            "新增 2 个文件 src/new-a.py、Makefile\n",
            "--in-flight-actual", "Makefile",
        )
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("Makefile", errors)

    def test_counted_list_accepts_cjk_root_filename(self):
        brief = "新增 2 个文件 docs/a.md、方案.md\n"
        result, output, errors = self.run_tool(brief)
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 2 brief path", output)
        result, _, errors = self.run_tool(
            brief, "--in-flight-actual", "方案.md"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("方案.md", errors)

    def test_counted_list_accepts_common_separators(self):
        for separator in (
            ";", "；", "&", "＆", "，以及 ", "，和 ", "，与 ",
            "、以及 ", "、和 ", "、与 ",
            ", and ", ",和 ", ", 和 ", ",以及 ", ", 以及 ",
            ",与 ", ", 与 ",
            "， 以及 ", "， 和 ", "， 与 ",
            "、 以及 ", "、 和 ", "、 与 ",
            "，and ", "， and ", "、and ", "、 and ",
        ):
            with self.subTest(separator=separator):
                result, _, errors = self.run_tool(
                    f"新增 2 个文件 src/new-a.py{separator}src/existing.py\n",
                    "--in-flight-actual", "src/existing.py",
                )
                self.assertEqual(result, 1)
                self.assertIn("marked new but already exists", errors)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("src/existing.py", errors)

    def test_explicit_new_markers_do_not_bleed_between_list_items(self):
        result, output, errors = self.run_tool(
            "新建 docs/new-a.md, 新建 docs/new-b.md；修改 docs/config.md\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 3 brief path", output)

        result, output, errors = self.run_tool(
            "新建 docs/new-a.md, docs/config.md；修改 docs/config.md\n"
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 2 brief path", output)

    def test_new_marker_for_existing_path_is_red(self):
        result, _, errors = self.run_tool("新建 src/existing.py\n")
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)

    def test_stdin_cannot_be_consumed_as_both_brief_and_list(self):
        for option in ("--in-flight-actual-list", "--in-flight-declared-list"):
            with self.subTest(option=option):
                result, _, errors = self.run_args(
                    [option, "-"], "Use src/overlap.py\nsrc/overlap.py\n"
                )
                self.assertEqual(result, 2)
                self.assertIn("stdin cannot be used for both", errors)

    def test_file_backed_in_flight_lists_are_read(self):
        for option in ("--in-flight-actual-list", "--in-flight-declared-list"):
            with self.subTest(option=option):
                list_file = self.root / f"{option[2:]}.txt"
                list_file.write_text("# changed files\nsrc/overlap.py\n", encoding="utf-8")
                result, _, errors = self.run_tool(
                    "Use src/overlap.py\n", option, str(list_file)
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("src/overlap.py", errors)

    def test_traversal_is_rejected(self):
        for path in ("../outside.py", r"..\outside.py"):
            with self.subTest(path=path):
                result, _, errors = self.run_tool(f"Use {path}\n")
                self.assertEqual(result, 1)
                self.assertIn("invalid or unsafe path", errors)


if __name__ == "__main__":
    unittest.main()
