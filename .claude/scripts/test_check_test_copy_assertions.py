#!/usr/bin/env python3
"""Self-test the whole-sentence copy assertion guard."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-test-copy-assertions.py")
BASELINE_PATH = "backend/tests/integration/test_domain_error_language.py"
BASELINE_COPY = "The source channel hasn't run yet (there's no session to clone)"
LONG_COPY = "This explanatory sentence should be checked by its important parts"


class TestCopyAssertionGuardTests(unittest.TestCase):
    def test_repository_and_frozen_baseline_pass(self) -> None:
        result = self._run(SCRIPT.parents[2])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_python_and_javascript_sentences_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_copy.py",
                f'assert actual == "{LONG_COPY}"\nself.assertEqual(actual, "{LONG_COPY}")\n',
            )
            self._write(
                root,
                "frontend/src/copy.spec.ts",
                f'expect(actual).toBe("{LONG_COPY}")\nexpect(actual).toEqual(`{LONG_COPY}`)\n'
                f'assert.equal(actual, "{LONG_COPY}")\n"{LONG_COPY}" === actual\n',
            )
            self._write(
                root,
                "backend/tests/unit/test_copy.cjs",
                f'assert.deepStrictEqual(actual, "{LONG_COPY}")\n',
            )

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("backend/tests/unit/test_copy.py:1", result.stderr)
        self.assertIn("backend/tests/unit/test_copy.py:2", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:1", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:2", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:3", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:4", result.stderr)
        self.assertIn("backend/tests/unit/test_copy.cjs:1", result.stderr)
        self.assertIn("Python 用 `in`", result.stderr)
        self.assertIn("Vitest 用 `toContain`", result.stderr)

    def test_baseline_is_exact_and_does_not_allow_an_extra_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assertion = f'assert actual == "{BASELINE_COPY}"\n'
            self._write(root, BASELINE_PATH, assertion + assertion)

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"{BASELINE_PATH}:2", result.stderr)
        self.assertIn("按必须表达的片段断言", result.stderr)

    def test_removed_baseline_assertions_are_reported_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("基线项已过期或数量减少", result.stderr)

    def test_negated_assertions_and_non_prose_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_values.py",
                '''
assert path == "https://example.test/api/a-long-route"
assert actual == f"{base}/projects/p-1/topics/t-1/threads/th-1?block={block}"
assert identifier == "a_long_identifier_name_that_is_not_copy"
assert response == "429 429 429 429 429 429 429 429 429 429"
assert status == "not found"
assert command == "/usr/local/bin/python3 scripts/run.py --project demo one two"
assert actual == "device link closed device=abc123 code=1000 after=42.0s"
assert actual == "since=2026-09-24T00:00:00Z\\nuntil=2026-09-24T05:59:59Z"
assert actual == "${{ inputs.integration && github.event_name != 'pull_request' }}"
assert actual == "github.ref_name && startsWith(github.ref, 'refs/heads/')"
assert actual == "SELECT name FROM users WHERE deleted_at IS NULL AND status IN ({})"
assert actual == "SELECT id, title FROM topics WHERE deleted_at IS NOT NULL ORDER BY id"
assert actual == "SELECT name FROM users WHERE deleted_at IS NOT NULL"
assert actual == "SELECT id, email FROM users WHERE status = active ORDER BY id"
assert actual == "SELECT id, email FROM users WHERE status = 'active'"
assert actual == "SELECT id, email FROM users WHERE id = $1"
assert actual == "SELECT id, email FROM users WHERE id = :user_id"
assert actual == "SELECT id, email FROM users WHERE id = ?"
assert actual == "SELECT id FROM events WHERE created_at > now() - interval '1 day'"
assert actual == "SELECT id, email, display_name FROM users ORDER BY created_at DESC LIMIT 20"
assert actual == "SELECT id, email FROM users u JOIN profiles p ON u.id = p.user_id"
assert actual == "SELECT a.id, a.email FROM accounts a JOIN profiles p ON a.id = p.account_id"
assert actual == "SELECT id, CASE WHEN status = 1 THEN 2 ELSE 0 END FROM users WHERE id = 1"
assert actual == "SELECT id FROM events WHERE can_retry = true AND deleted_at IS NULL"
assert actual == "SELECT id, payload FROM queue_items WHERE next = $1"
assert actual == "SELECT message FROM notifications ORDER BY id DESC"
assert actual == "SELECT id FROM project WHERE id = 'active'"
assert actual == "SELECT id, email FROM users GROUP BY team_id ORDER BY team_id"
assert actual == "SELECT id FROM events WHERE created_at = now() - interval '1 day'"
assert actual == "select id, email from users where status = active order by id"
assert actual == "WHERE users.owner_id = topics.author_id AND users.created_at = topics.updated_at"
assert actual == "WHERE deleted_at IS NULL AND completion_status IN ({})"
assert actual == "Type 'Foo' has no property 'bar' in this version of the library"
assert actual == "Type 'SaveResult' does not satisfy the constraint of type 'Save': ok"
assert actual == "Type 'Foo' is missing the following properties from type 'Save': ok, id"
assert actual == "Type 'Record' has no properties in common with type 'Save'"
assert actual == "Type 'Foo' cannot be used as an index type."
assert actual == "Type 'Foo' does not have any construct or call signatures."
assert actual == "Type 'Foo' is incompatible with type 'Bar'."
assert actual == "Type 'Foo' is not comparable to type 'Bar'."
assert actual == "Type 'Foo' has no call signatures and cannot be invoked."
assert actual != "This explanatory sentence should be checked by its important parts"
assert "archived/removed notices should preserve the relevant message for readers" in text
''',
            )
            self._write(
                root,
                "frontend/src/values.spec.ts",
                '''
expect(url).toBe('/topics/a-long-route')
expect(templateUrl).toBe(`${primary}/users/auth/oauth/login/github?state=st&access_type=offline`)
expect(code).toBe('a_long_identifier_name_that_is_not_copy')
expect(actual).not.toBe("This explanatory sentence should be checked by its important parts")
''',
            )

            result = self._run(root)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("test_values.py:", result.stderr)
        self.assertNotIn("values.spec.ts:", result.stderr)
        self.assertIn("基线项已过期或数量减少", result.stderr)

    def test_sql_prose_paths_routes_and_diagnostics_keep_their_boundaries(self) -> None:
        values = [
            "select item, quantity from inventory before ordering more stock",
            "select files from folder where size > 10 before upload",
            "/home/cheese/My Documents/very long project name/final report.docx",
            "/var/lib/cheese/some/very/long/directory/name/",
            "?block=blk-1&mode=full&tab=settings&page=2",
            "#/projects/p-1/topics/t-1/threads?tab=settings",
            "SELECT it.id, it.email FROM items it WHERE it.deleted_at IS NULL",
            "SELECT identifier FROM users WHERE status = %(status)s",
            "SELECT identifier FROM users WHERE status = %s",
            "select id from users where deleted_at is not null",
            "Property 'title' does not exist on type 'Topic'.",
            "Argument of type 'string' is not assignable to parameter of type 'number'.",
            "Cannot find module './missing' or its corresponding type declarations.",
            "No overload matches this call",
            "Object literal may only specify known properties, and 'foo' does not exist in type 'Bar'.",
            "Element implicitly has an 'any' type because expression of type 'string' can't be used to index type 'Foo'.",
            "Module '\"./missing\"' has no exported member 'Thing'.",
            "format('{0}/{1}/{2}', github.repository, github.ref_name, github.sha)",
            "startsWith(github.ref, 'refs/heads/') && github.event_name == 'pull_request'",
            "contains(fromJSON('[...]'), github.event_name)",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = "\n".join(f"assert value_{index} == {value!r}" for index, value in enumerate(values, 1))
            self._write(root, "backend/tests/unit/test_boundaries.py", source)

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("test_boundaries.py:1:", result.stderr)
        self.assertIn("test_boundaries.py:2:", result.stderr)
        for line in range(3, len(values) + 1):
            self.assertNotIn(f"test_boundaries.py:{line}:", result.stderr)

    def test_explicit_root_is_scanned_when_scanner_script_is_shallow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture"
            self._write(fixture, "backend/tests/unit/test_copy.py", f'assert actual == "{LONG_COPY}"')
            script = root / "scanner.py"
            script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--root", str(fixture)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("backend/tests/unit/test_copy.py:1", result.stderr)

    def test_chinese_filename_command_words_and_multiline_copy_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_copy.py",
                '''
assert actual == "这个团队已经有这个成员了，无法重复添加该成员"
assert actual == "answer-first.md 的这一版已经改不动了：它已经存在（现在是第 1 版）。重读一次，把改动并进去，再写。"
assert actual == "The task can only be assigned to one person in this workspace"
assert actual == "Please run python from the project root after the environment starts"
assert actual == "Select one option and then continue with the next step"
assert actual == "The dialog explains where the file should be saved after the process finishes"
assert response.name == "The dialog explains where the file should be saved after the process finishes"
assert actual == "Select a message from the list where it belongs and continue with the next step"
assert actual == "Select a message from the list and join the discussion in the main room"
assert actual == "Select the option from the menu where name = weekly and continue with the next step"
assert actual == "select the option from the menu where name = weekly and continue with the next step"
assert actual == "select records from archive, then continue to the next page for more detail"
assert actual == "SELECT failed because the row from the table where id = 3 is missing"
assert actual == "Type a message to continue and then press Enter to send it."
assert actual == "Type 'Save' does not work in this browser, choose another option to continue"
assert actual == "The deployment failed because the gateway could not reach the upstream.\\nPlease retry after the environment has started."
assert actual == "deleted_at IS NULL AND completion_status IN ({})"
''',
            )
            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        for line in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
            self.assertIn(f"test_copy.py:{line}", result.stderr)
        self.assertNotIn("test_copy.py:18", result.stderr)

    def test_python_fstrings_and_wrapped_vitest_arguments_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_fstring.py",
                "\n".join(
                    [
                        f'assert actual == f"{LONG_COPY} {{item}}"',
                        f'self.assertEqual(actual, f"{LONG_COPY} {{item}}")',
                    ]
                ),
            )
            self._write(
                root,
                "frontend/src/copy.spec.ts",
                f'''let index = 0; index++ / 2; expect(actual).toBe("{LONG_COPY}"); const half = 3 / 4;
const quote = /["']/;
expect(actual).toBe(("{LONG_COPY}" as const));
expect(actual).toEqual("{LONG_COPY}" + " suffix");
expect(actual).toEqual(["{LONG_COPY}"]);
expect(actual).toBe(normalize("{LONG_COPY}"));
expect(actual).toBe(joinWords(["{LONG_COPY}", "other"]));
expect(actual).toBe(fn?.("{LONG_COPY}"));
expect(actual).toBe(!(flag ? "{LONG_COPY}" : "x"));
expect(actual).toBe(fn!("{LONG_COPY}"));
expect(actual).toBe(mapping["{LONG_COPY}"]);
''',
            )
            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("test_fstring.py:1", result.stderr)
        self.assertIn("test_fstring.py:2", result.stderr)
        self.assertIn("copy.spec.ts:1", result.stderr)
        self.assertIn("copy.spec.ts:3", result.stderr)
        self.assertIn("copy.spec.ts:4", result.stderr)
        self.assertIn("copy.spec.ts:5", result.stderr)
        self.assertNotIn("copy.spec.ts:6", result.stderr)
        self.assertNotIn("copy.spec.ts:7", result.stderr)
        self.assertNotIn("copy.spec.ts:8", result.stderr)
        self.assertNotIn("copy.spec.ts:9", result.stderr)
        self.assertNotIn("copy.spec.ts:10", result.stderr)
        self.assertNotIn("copy.spec.ts:11", result.stderr)

    def test_urls_mentioned_in_copy_are_not_filtered_as_path_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_links.py",
                'assert actual == "Open https://example.test/help if the browser does not show the correct account settings."\n'
                'assert actual == "Open https://example.test/help?mode=brief&source=test and continue with the next step please."',
            )
            self._write(
                root,
                "frontend/src/links.spec.ts",
                "expect(url).toBe('https://example.test/api/help?mode=brief&source=test')",
            )
            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("test_links.py:1", result.stderr)
        self.assertIn("test_links.py:2", result.stderr)
        self.assertNotIn("links.spec.ts:", result.stderr)

    def test_additional_test_roots_are_scanned(self) -> None:
        roots = (".github/scripts", "backend/scripts", "deploy/gateway", "deploy/tests", "docs/site/test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for scan_root in roots:
                self._write(root, f"{scan_root}/test_copy.py", f'assert actual == "{LONG_COPY}"')
            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        for scan_root in roots:
            self.assertIn(f"{scan_root}/test_copy.py:1", result.stderr)

    @staticmethod
    def _write(root: Path, relative_path: str, content: str) -> None:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    @staticmethod
    def _run(root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
