#!/usr/bin/env python3
"""Reject fragile whole-sentence equality assertions in tests."""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections import Counter
from pathlib import Path


# Existing assertions are allowed only at this exact path/text and count.
SCAN_ERRORS: list[str] = []
BASELINE: dict[tuple[str, str], int] = {
    ("backend/tests/integration/test_cloud_compute_billing.py", "平台提供的环境暂时无法启动：平台还没有设定算力价格。对话和平台工具仍可用。"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "The source channel hasn't run yet (there's no session to clone)"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "The frequency can only be one of hourly, daily, weekly, monthly"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "There can be only one time window of each length"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "The document collaboration service can't be reached right now. Try again later"): 1,
    ("backend/tests/integration/test_team_refusals_are_said_in_the_readers_language.py", "This person is already a member of this team"): 2,
    ("backend/tests/integration/test_team_refusals_are_said_in_the_readers_language.py", "This person already has an invitation or join request for this team waiting to be answered"): 1,
    ("backend/tests/integration/test_whole_cloud_vm.py", "虚拟机沙箱暂时无法启动：平台还没有设定这种规格的价格。对话和平台工具仍可用。"): 1,
    ("backend/tests/unit/test_claude_channel_startup.py", "Claude Code 启动失败：这个频道上一个会话进程还没有退出"): 1,
    ("backend/tests/unit/test_claude_channel_startup.py", "Claude Code 启动失败：在等待时限内没有起来，启动记录在现场"): 1,
    ("backend/tests/unit/test_commit_message.py", "The commit title can only be one line. Put the explanation in the body"): 1,
    ("backend/tests/unit/test_platform_failures.py", "Claude Code 启动失败：在等待时限内没有起来，启动记录在现场"): 1,
    ("backend/tests/unit/test_sentences.py", "PR #12 is ready to merge, waiting for ana to accept"): 1,
    ("frontend/src/api/eventStream.spec.ts", "Only the invited person can answer this invitation"): 1,
    ("frontend/src/apiErrorLanguage.spec.ts", "This person has already been invited and hasn't answered yet"): 1,
    ("frontend/src/apiErrorLanguage.spec.ts", "The environment variable PATH is reserved or isn't a valid name"): 1,
    ("frontend/src/lib/adminPool.spec.ts", "4 张账号：1 张冷却中、1 张已停用、1 张状态未知，最早 14:20 恢复"): 1,
    ("frontend/src/lib/adminPool.spec.ts", "Cooling until 02:20 PM · 2 consecutive failures"): 1,
    ("frontend/src/views/tasks/composables/useAssistant.spec.ts", "Cheese can't answer right now. Try again later."): 1,
    ("frontend/src/views/account/attemptWait.spec.ts", "Too many attempts. Try again in ${wait}."): 1,
}

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".next",
    ".turbo",
}
TEST_SUFFIXES = {".py", ".js", ".jsx", ".cjs", ".mjs", ".ts", ".tsx", ".cts", ".mts", ".vue"}
TOKEN_RE = re.compile(
    r"(?P<space>\s+)|(?P<line_comment>//[^\n]*)|(?P<block_comment>/\*.*?\*/)|"
    r"(?P<string>`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")|"
    r"(?P<identifier>[A-Za-z_$][\w$]*)|(?P<number>\d+(?:\.\d+)?)|"
    r"(?P<operator>===|!==|==|!=|=>|\?\.|&&|\|\||.)",
    re.DOTALL,
)
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
PATH_RE = re.compile(
    r"\b(?:https?|wss?)://|(?:^|[\s(\"'`])(?:\.\.?/|/)(?:[\w.-]+/)*[\w.-]+|"
    r"(?:^|[\s(\"'`])[\w.-]+(?:/[\w.-]+){2,}|"
    r"\b[\w.-]+\.(?:md|py|ts|tsx|js|jsx|cjs|mjs|json|txt|vue|docx)\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
COMMIT_TITLE_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor|test|build|ci)(?:\([^)]*\))?: ", re.IGNORECASE)
KEY_VALUE_RE = re.compile(r"\b[\w-]+=\S+")
IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*\Z")
STATUS_CODES_RE = re.compile(r"(?:HTTP\s*)?\d{3}(?:\s+\d{3})*\Z", re.IGNORECASE)
SHELL_COMMAND_RE = re.compile(r"\b(?:git\s+(?:fetch|merge|checkout|switch|status|diff)\b|npm\b|pnpm\b|python3?\b|task\b)", re.IGNORECASE)
JS_ASSERTION_METHODS = {
    "toBe",
    "toEqual",
    "toStrictEqual",
    "equal",
    "strictEqual",
    "deepEqual",
    "deepStrictEqual",
}


def _test_files(root: Path) -> list[Path]:
    scan_roots = [
        root / ".claude" / "scripts",
        root / "backend" / "tests",
        root / "frontend",
        root / "desktop",
        root / "e2e",
        root / "scripts",
    ]
    files: set[Path] = set()
    for scan_root in scan_roots:
        if not scan_root.is_dir():
            continue
        for directory, child_dirs, filenames in os.walk(scan_root):
            child_dirs[:] = [name for name in child_dirs if name not in SKIP_DIRS]
            for filename in filenames:
                path = Path(directory) / filename
                if path.suffix.lower() not in TEST_SUFFIXES:
                    continue
                name = path.name.lower()
                if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
                    files.add(path)
    return sorted(files)


def _is_explanatory_sentence(value: str) -> bool:
    value = value.strip()
    if (
        len(value) < 32
        or "\n" in value
        or PATH_RE.search(value)
        or EMAIL_RE.search(value)
        or COMMIT_TITLE_RE.match(value)
        or KEY_VALUE_RE.search(value)
        or SHELL_COMMAND_RE.search(value)
        or value.startswith("<")
        or IDENTIFIER_RE.fullmatch(value)
        or STATUS_CODES_RE.fullmatch(value)
        or value.startswith(("Co-authored-by:", "::error::", "## "))
    ):
        return False
    han_count = len(HAN_RE.findall(value))
    if han_count >= 18:
        return True
    words = WORD_RE.findall(value)
    if len(value) < 40 or len(words) < 7:
        return False
    # Permission/capability lists and similar structured values are not prose.
    if value.count(":") >= 2 and value.count(",") >= 1:
        return False
    return True


def _python_literals(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        SCAN_ERRORS.append(f"{path}: 无法扫描测试文件: {exc}")
        return []
    literals: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            for operator, comparator in zip(node.ops, node.comparators):
                if isinstance(operator, ast.Eq):
                    for candidate in (left, comparator):
                        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                            literals.append((candidate.lineno, candidate.value))
                left = comparator
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"assertEqual", "assertEquals"}
        ):
            for candidate in node.args[:2]:
                if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                    literals.append((candidate.lineno, candidate.value))
    return literals


def _decode_js_string(token: str) -> str:
    content = token[1:-1]
    replacements = {
        "\\n": "\n",
        "\\r": "\r",
        "\\t": "\t",
        "\\\\": "\\",
        "\\'": "'",
        '\\"': '\"',
    }
    for escaped, decoded in replacements.items():
        content = content.replace(escaped, decoded)
    return content


def _is_js_string(token: str) -> bool:
    return len(token) >= 2 and token[0] in {"'", '\"', "`"} and token[-1] == token[0]


def _call_argument_strings(tokens: list[tuple[str, int]], open_index: int) -> list[tuple[int, str]]:
    arguments: list[list[tuple[str, int]]] = [[]]
    depth = 0
    for token, line in tokens[open_index + 1 :]:
        if token == ")" and depth == 0:
            break
        if token in {"(", "[", "{"}:
            depth += 1
        elif token in {")", "]", "}"}:
            depth -= 1
        if token == "," and depth == 0:
            if len(arguments) == 2:
                break
            arguments.append([])
        elif len(arguments) <= 2:
            arguments[-1].append((token, line))
    literals: list[tuple[int, str]] = []
    for argument in arguments[:2]:
        if len(argument) == 1 and _is_js_string(argument[0][0]):
            token, line = argument[0]
            literals.append((line, _decode_js_string(token)))
    return literals


def _javascript_literals(path: Path) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        SCAN_ERRORS.append(f"{path}: 无法扫描测试文件: {exc}")
        return []

    tokens: list[tuple[str, int]] = []
    line = 1
    for match in TOKEN_RE.finditer(source):
        token = match.group()
        kind = match.lastgroup
        if kind in {"space", "line_comment", "block_comment"}:
            line += token.count("\n")
            continue
        tokens.append((token, line))
        line += token.count("\n")

    literals: list[tuple[int, str]] = []
    for index, (token, _) in enumerate(tokens):
        if token in {"==", "==="}:
            for candidate_index in (index - 1, index + 1):
                if 0 <= candidate_index < len(tokens):
                    candidate, candidate_line = tokens[candidate_index]
                    if _is_js_string(candidate):
                        literals.append((candidate_line, _decode_js_string(candidate)))
        if token not in JS_ASSERTION_METHODS or index + 1 >= len(tokens) or tokens[index + 1][0] != "(":
            continue
        if index >= 3 and tokens[index - 1][0] == "." and tokens[index - 2][0] == "not":
            continue
        if index >= 1 and tokens[index - 1][0] == ".":
            literals.extend(_call_argument_strings(tokens, index + 1))
    return literals


def _findings(root: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in _test_files(root):
        literals = _python_literals(path) if path.suffix == ".py" else _javascript_literals(path)
        relative = path.relative_to(root).as_posix()
        findings.extend(
            (relative, line, value)
            for line, value in literals
            if _is_explanatory_sentence(value)
        )
    return findings


def check(root: Path) -> int:
    SCAN_ERRORS.clear()
    findings = _findings(root)
    for error in SCAN_ERRORS:
        print(error, file=sys.stderr)
    if SCAN_ERRORS:
        return 1
    errors = 0
    allowed = Counter(BASELINE)
    for path, line, value in findings:
        key = (path, value)
        if allowed[key]:
            allowed[key] -= 1
            continue
        print(
            f"{path}:{line}: 测试对长说明文案做了整句相等比较；"
            "改为按必须表达的片段断言（Python 用 `in`，Vitest 用 `toContain`）",
            file=sys.stderr,
        )
        errors += 1
    for (path, value), remaining in allowed.items():
        if remaining:
            print(f"基线项已过期或数量减少：{path}: {value!r}（缺少 {remaining} 次）", file=sys.stderr)
            errors += remaining
    if errors:
        print(f"测试文案断言守卫发现 {errors} 个新增问题或失配基线项", file=sys.stderr)
        return 1
    print(f"测试文案断言守卫通过（{sum(BASELINE.values())} 个精确历史基线项已冻结）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = args.root if args.root is not None else Path(__file__).resolve().parents[2]
    return check(root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
