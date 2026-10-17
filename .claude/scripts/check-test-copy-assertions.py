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
    ("backend/tests/integration/test_admin_and_form_error_language.py", "环境变量 PATH 是保留名，或者不是合法的变量名"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "answer-first.md 的这一版已经改不动了：它已经存在（现在是第 1 版）。重读一次，把改动并进去，再写。"): 1,
    ("backend/tests/integration/test_domain_error_language.py", "This version of answer-first.md can no longer be changed: it already exists (now at version 1). Read it again, merge your changes in, then write."): 1,
    ("backend/tests/integration/test_domain_error_language.py", "Not a docs page: ../../etc/passwd (write the page name, such as accept or dev/turn)"): 1,
    ("backend/tests/integration/test_room_sees_the_work.py", "分身查完了：查分页接口现状\n结论：分页用的是 offset， 改动点在路由层"): 1,
    ("backend/tests/integration/test_team_refusals_are_said_in_the_readers_language.py", "这个人已经有一条加入这个团队的邀请或申请在等处理"): 1,
    ("backend/tests/unit/test_event_lines.py", "分身查完了：查分页接口现状\n结论： 分页用的是 offset"): 1,
    ("backend/tests/unit/test_local_fs_paths.py", "“NUL.txt” is a reserved Windows device name"): 1,
    ("frontend/src/App.newProjectIntent.spec.ts", "帮我把这学期的课程材料整理成一份大纲"): 1,
    ("frontend/src/components/panels/PanelPreview.document.spec.ts", "在 output/评审简报.docx 的 第 2 页（「平台在真实课程中完成了部署」）：这句话和摘要对不上"): 1,
    ("frontend/src/components/panels/PanelPreview.document.spec.ts", "在 output/说明.md 的 标题「配置」（「重试 3 次」）：这里也要写清楚"): 1,
    ("frontend/src/components/panels/doc/blocks/reader.spec.ts", "组会报名\n时间：周四下午三点\n地点：B201\n报名找林老师"): 1,
    ("frontend/src/lib/platformEvents.spec.ts", "这轮因运行环境存储空间不足而暂停，项目文件和已完成的改动都还在。"): 1,
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
PATH_VALUE_RE = re.compile(
    r"(?:https?|wss?)://[^\s]+|"
    r"(?:[A-Za-z]:)?[\\/][\w.-]+(?:[\\/][\w.-]+)*|"
    r"\.{1,2}[\\/][\w.-]+(?:[\\/][\w.-]+)*|"
    r"[\w.-]+(?:[\\/][\w.-]+)+|"
    r"[\w.-]+\.(?:md|py|ts|tsx|js|jsx|cjs|mjs|json|txt|vue|docx)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
COMMIT_TITLE_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor|test|build|ci)(?:\([^)]*\))?: ", re.IGNORECASE)
KEY_VALUE_RE = re.compile(r"\b[\w-]+=\S+")
IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*\Z")
STATUS_CODES_RE = re.compile(r"(?:HTTP\s*)?\d{3}(?:\s+\d{3})*\Z", re.IGNORECASE)
WORKFLOW_EXPRESSION_RE = re.compile(
    r"(?:needs|steps|github|matrix|env|vars|inputs)\.[\w.-]+"
    r"(?:\s*(?:==|!=|&amp;&amp;|\|\|).*)?\Z",
    re.IGNORECASE,
)
SHELL_COMMAND_RE = re.compile(
    r"(?:^|&&\s*)(?:git\s+(?:fetch|merge|checkout|switch|status|diff)\b|"
    r"(?:npm|pnpm)\s+(?:install|ci|test|run|exec)\b|"
    r"python3?\s+(?:-[mcu]\b|\S+\.py\b)|(?:sh|bash)\s+\S+)|^workflow\s+run\b",
    re.IGNORECASE,
)
TITLE_ACTION_RE = re.compile(r"^(?:Require|Ensure|Check|Run|Use|Add|Remove|Enter|Click|Select)\b")
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
        root / ".github" / "scripts",
        root / "backend" / "scripts",
        root / "backend" / "tests",
        root / "deploy" / "gateway",
        root / "deploy" / "tests",
        root / "docs" / "site",
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
    value = " ".join(value.split())
    han_count = len(HAN_RE.findall(value))
    if (
        not value
        or PATH_VALUE_RE.fullmatch(value)
        or EMAIL_RE.fullmatch(value)
        or COMMIT_TITLE_RE.match(value)
        or KEY_VALUE_RE.search(value)
        or SHELL_COMMAND_RE.search(value)
        or WORKFLOW_EXPRESSION_RE.fullmatch(value)
        or value.startswith(("<", ":::" , "import ", "src/", "backend/", "frontend/", "deploy/", "可递：", "missing:", "since=", "Type "))
        or value.count("|") >= 2
        or ":::" in value
        or value.startswith("step ")
        or re.search(r"\b(?:IS\s+NULL|IN\s*\(\{\}\)|SELECT|FROM|WHERE)\b", value, re.IGNORECASE)
        or (EMAIL_RE.search(value) and not han_count and len(WORD_RE.findall(value)) < 9)
        or IDENTIFIER_RE.fullmatch(value)
        or STATUS_CODES_RE.fullmatch(value)
        or value.startswith(("Co-authored-by:", "::error::", "## "))
        or (not han_count and (value.startswith(("/", "./", "../", "http")) or "://" in value or ("?" in value and "/" in value)))
        or (not re.search(r"[.!?:;]", value) and TITLE_ACTION_RE.match(value))
    ):
        return False
    if han_count >= 18:
        return True
    words = WORD_RE.findall(value)
    if len(value) < 40 or len(words) < 7:
        return False
    # Permission/capability lists and similar structured values are not prose.
    if value.count(":") >= 2 and value.count(",") >= 1:
        return False
    return True


def _python_string_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                parts.append("{}")
        return "".join(parts)
    return None


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
                        value = _python_string_value(candidate)
                        if value is not None:
                            literals.append((candidate.lineno, value))
                left = comparator
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"assertEqual", "assertEquals"}
        ):
            for candidate in node.args[:2]:
                value = _python_string_value(candidate)
                if value is not None:
                    literals.append((candidate.lineno, value))
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
        literals.extend(
            (line, _decode_js_string(token))
            for token, line in argument
            if _is_js_string(token)
        )
    return literals


def _regex_literal_end(source: str, start: int) -> int | None:
    in_character_class = False
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if char in "\r\n":
            return None
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            in_character_class = True
        elif char == "]":
            in_character_class = False
        elif char == "/" and not in_character_class:
            index += 1
            while index < len(source) and source[index].isalpha():
                index += 1
            return index
    return None


def _can_start_regex(tokens: list[tuple[str, int]]) -> bool:
    if not tokens:
        return True
    return tokens[-1][0] in {
        "(", "[", "{", ",", ";", ":", "=", "==", "===", "!", "!=", "!==",
        "?", "&&", "||", "??", "=>", "+", "-", "*", "%", "~", "^", "&", "|",
        "return", "throw", "case", "delete", "void", "typeof", "instanceof", "in", "of",
        "yield", "await",
    }


def _javascript_tokens(source: str) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    position = 0
    line = 1
    while position < len(source):
        if (
            source[position] == "/"
            and not source.startswith(("//", "/*"), position)
            and _can_start_regex(tokens)
        ):
            end = _regex_literal_end(source, position)
            if end is not None:
                token = source[position:end]
                tokens.append((token, line))
                position = end
                continue
        match = TOKEN_RE.match(source, position)
        if match is None:
            position += 1
            continue
        token = match.group()
        if match.lastgroup in {"space", "line_comment", "block_comment"}:
            line += token.count("\n")
        else:
            tokens.append((token, line))
            line += token.count("\n")
        position = match.end()
    return tokens


def _javascript_literals(path: Path) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        SCAN_ERRORS.append(f"{path}: 无法扫描测试文件: {exc}")
        return []

    tokens = _javascript_tokens(source)

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
