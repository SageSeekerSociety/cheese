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
    ("frontend/src/components/account/SudoDialog.spec.ts", "Enter a code from your authenticator app"): 4,
    ("backend/tests/unit/test_observe_ci_resources.py", "hand the run to CI when the local box is shared"): 1,
    ("deploy/tests/test_pr_watch_deploy.py", "Require images and tests for the same commit"): 1,
    ("frontend/src/lib/adminPool.spec.ts", "2 accounts: 1 cooling, 1 disabled — earliest recovery 02:20 PM"): 1,
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
    r"(?P<operator>\+\+|--|===|!==|==|!=|=>|\?\.|&&|\|\||.)",
    re.DOTALL,
)
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
PATH_VALUE_RE = re.compile(
    r"(?:https?|wss?)://[^\s]+|"
    r"(?:(?:[A-Za-z]:)?[\\/][\w.%+${}:-]+(?:[\\/][\w.%+${}:-]+)*|"
    r"\.{1,2}[\\/][\w.%+${}:-]+(?:[\\/][\w.%+${}:-]+)*|"
    r"[\w.%+${}:-]+(?:[\\/][\w.%+${}:-]+)+|"
    r"[\w.-]+\.(?:md|py|ts|tsx|js|jsx|cjs|mjs|json|txt|vue|docx))(?:\?[^\s]+)?",
    re.IGNORECASE,
)
ABSOLUTE_PATH_VALUE_RE = re.compile(r"^(?:[A-Za-z]:)?[\\/][^\r\n]*[\\/][^\r\n]*$")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
COMMIT_TITLE_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor|test|build|ci)(?:\([^)]*\))?: ", re.IGNORECASE)
KEY_VALUE_RE = re.compile(r"\b[\w-]+=\S+")
STRUCTURED_ACTION_LABEL = (
    r"(?:read|write|admin|export|create|update|delete|manage|execute|list|view|invite|approve|"
    r"download|upload|deploy|access|run|edit|review|import|share)"
)
STRUCTURED_PERMISSION_LEVELS = {"read", "write", "admin", "none"}
STRUCTURED_PERMISSION_RESOURCES = {
    "actions", "administration", "attestations", "checks", "code", "code-quality", "code_quality",
    "codespaces", "contents", "deployments", "discussions", "emails", "environments", "followers",
    "gpg_keys", "gpg-keys", "id-token", "id_token", "interaction_limits", "interaction-limits",
    "issues", "keys", "members", "metadata", "models", "organization_administration",
    "organization_custom_properties", "organization_events", "organization_hooks", "organization_packages",
    "organization_plan", "organization_projects", "organization_self_hosted_runners",
    "organization_user_blocking", "packages", "pages", "plan", "pull-requests", "pull_requests",
    "repository_custom_properties", "repository-custom-properties", "repository_hooks",
    "repository_projects", "repository-projects", "security_events", "security-events",
    "secret_scanning_alerts", "secret-scanning-alerts", "statuses", "starring", "team_discussions",
    "vulnerability_alerts", "vulnerability-alerts", "workflows", "artifact_metadata", "artifact-metadata",
    "copilot_requests", "copilot-requests",
}
STRUCTURED_LABEL_LIST_RE = re.compile(
    rf"^{STRUCTURED_ACTION_LABEL}:\s*[^,:]+(?:,\s*{STRUCTURED_ACTION_LABEL}:\s*[^,:]+)+$",
    re.IGNORECASE,
)
IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*\Z")
STATUS_CODES_RE = re.compile(r"(?:HTTP\s*)?\d{3}(?:\s+\d{3})*\Z", re.IGNORECASE)
STEP_DIAGNOSTIC_RE = re.compile(
    r"^step\s+\d+[^:]*:\s+(?:panel is [\w-]+, but no step declares [\w-]+|changes has no files)$",
    re.IGNORECASE,
)
UTILITY_CLASS_TOKEN_RE = re.compile(r"[\w:[\].#%/-]+\Z")
UTILITY_CLASS_PREFIXES = (
    "items-", "justify-", "border-", "bg-", "text-", "p-", "px-", "py-", "pt-", "m-",
    "mb-", "mt-", "w-", "h-", "gap-", "space-", "rounded-", "shadow-", "font-",
    "leading-", "max-w-", "min-w-", "overflow-", "cursor-", "transition-", "opacity-",
    "z-", "top-", "left-", "right-", "bottom-", "flex-", "whitespace-",
)
UTILITY_CLASS_SINGLETONS = {
    "flex", "inline-flex", "grid", "block", "hidden", "inline", "absolute", "relative",
    "fixed", "sticky", "truncate", "sr-only",
}
WORKFLOW_EXPRESSION_RE = re.compile(
    r"(?:\$\{\{.*\}\}|\(*\s*!?\s*(?:"
    r"(?:needs|steps|github|matrix|env|vars|inputs)\.[\w.-]+"
    r"(?:\s*(?:==|!=|&&|\|\|).*)?|"
    r"(?:format|startsWith|endsWith|contains|fromJSON|toJSON|join|hashFiles|"
    r"success|failure|always|cancelled)\s*\(.*\)(?:\s*(?:==|!=|&&|\|\|).*)?)\s*\)*)\Z",
    re.IGNORECASE | re.DOTALL,
)
SHELL_COMMAND_RE = re.compile(
    r"(?:^|&&\s*)(?:/(?:[\w.-]+/)*(?:git|npm|pnpm|python3?|sh|bash)\s+\S+|"
    r"git\s+(?:fetch|merge|checkout|switch|status|diff)\b|"
    r"(?:npm|pnpm)\s+(?:install|ci|test|run|exec)\b|"
    r"python3?\s+(?:-[mcu]\b|\S+\.py\b)|(?:sh|bash)\s+\S+)|^workflow\s+run\b",
    re.IGNORECASE,
)
SQL_EXPRESSION_RE = re.compile(
    r"\w+(?:\.\w+)?\s+IS\s+(?:NOT\s+)?NULL\s+AND\s+\w+(?:\.\w+)?\s+IN\s*\(\{\}\)",
    re.IGNORECASE,
)
SQL_QUERY_RE = re.compile(
    r"(?:SELECT\b.*\bFROM\b.*"
    r"(?:\bJOIN\b.*\bON\b|\bORDER\s+BY\b|\bGROUP\s+BY\b|\bLIMIT\b|"
    r"\bIS\s+(?:NOT\s+)?NULL\b|\bIN\s*\(|"
    r"\w+\s+(?:LIKE|ILIKE)\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[\w.]+)|"
    r"\w+\s+BETWEEN\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+)\s+AND\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+)|"
    r"\w+\s*(?:=|!=|<>|<=|>=|<|>)\s*(?:'[^']*'|\"[^\"]*\"|\$\d+|"
    r":\w+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|"
    r"[\w.]+(?:\([^)]*\))?))).*",
    re.DOTALL | re.IGNORECASE,
)
SQL_WHERE_RE = re.compile(
    r"WHERE\s+[\w.]+\s*(?:IS\s+(?:NOT\s+)?NULL|IN\s*\([^)]*\)|"
    r"LIKE\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[\w.]+)|"
    r"BETWEEN\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+)\s+AND\s+(?:'[^']*'|\"[^\"]*\"|\$\d+|:[\w]+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+)|"
    r"(?:=|!=|<>|<=|>=|<|>)\s*(?:'[^']*'|\"[^\"]*\"|\$\d+|"
    r":\w+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+(?:\([^)]*\))?)).*",
    re.DOTALL | re.IGNORECASE,
)
SQL_VALUE_PATTERN = r"(?:'[^']*'|\"[^\"]*\"|\$\d+|:\w+|\?|%\([^)]+\)s|%s|[-+]?\d+(?:\.\d+)?|[\w.]+(?:\([^)]*\))?)(?:\s*::\s*[\w.]+(?:\s*\([^)]*\))?)?"
SQL_COLUMN_PATTERN = r"[\w.]+(?:\s*(?:->>|->|#>>|#>)\s*(?:'[^']*'|\"[^\"]*\"|[\w.]+))*"
SQL_JSON_OPERATOR_PATTERN = r"(?:@>|<@|\?\||\?&|\?|=|!=|<>|<=|>=|<|>)"
SQL_PREDICATE_PATTERN = (
    rf"(?:IS\s+(?:NOT\s+)?NULL|IN\s*\([^)]*\)|LIKE\s+{SQL_VALUE_PATTERN}|"
    rf"BETWEEN\s+{SQL_VALUE_PATTERN}\s+AND\s+{SQL_VALUE_PATTERN}|"
    rf"{SQL_JSON_OPERATOR_PATTERN}\s*{SQL_VALUE_PATTERN})"
)
SQL_RETURNING_ITEM_PATTERN = (
    rf"(?:\*|{SQL_COLUMN_PATTERN}(?:\.\*)?)"
    rf"(?:\s*::\s*[\w.]+(?:\s*\([^)]*\))?)?"
    rf"(?:\s+AS\s+[\w]+)?(?:\s+IS\s+(?:NOT\s+)?NULL)?(?:\s+AS\s+[\w]+)?"
)
SQL_RETURNING_PATTERN = (
    rf"(?:\s+RETURNING\s+{SQL_RETURNING_ITEM_PATTERN}"
    rf"(?:\s*,\s*{SQL_RETURNING_ITEM_PATTERN})*)?"
)
SQL_UPDATE_RE = re.compile(
    rf"UPDATE\s+[\w.]+\s+SET\s+[\w.]+\s*=\s*{SQL_VALUE_PATTERN}"
    rf"(?:\s*,\s*[\w.]+\s*=\s*{SQL_VALUE_PATTERN})*\s+WHERE\s+{SQL_COLUMN_PATTERN}\s*{SQL_PREDICATE_PATTERN}"
    rf"{SQL_RETURNING_PATTERN}",
    re.IGNORECASE,
)
SQL_DELETE_RE = re.compile(
    rf"DELETE\s+FROM\s+[\w.]+\s+WHERE\s+{SQL_COLUMN_PATTERN}\s*{SQL_PREDICATE_PATTERN}{SQL_RETURNING_PATTERN}",
    re.IGNORECASE,
)
SQL_INSERT_RE = re.compile(
    rf"INSERT\s+INTO\s+[\w.]+\s*\([^)]*\)\s*VALUES\s*\([^)]*\)(?:\s*,\s*\([^)]*\))*"
    rf"(?:\s+ON\s+CONFLICT(?:\s*\([^)]*\))?\s+DO\s+(?:NOTHING|UPDATE\s+SET\s+[\w.]+\s*=\s*{SQL_VALUE_PATTERN}(?:\s*,\s*[\w.]+\s*=\s*{SQL_VALUE_PATTERN})*))?"
    rf"{SQL_RETURNING_PATTERN}",
    re.IGNORECASE,
)
SQL_SELECT_PREFIX_RE = re.compile(r"SELECT\s+(.+?)\s+FROM\s+([\w.]+)", re.DOTALL | re.IGNORECASE)
SQL_WITH_QUERY_RE = re.compile(
    r"WITH\s+(?:RECURSIVE\s+)?[\w.]+\s+AS\s*\(.*\)\s*(?:SELECT|INSERT|UPDATE|DELETE)\b.*",
    re.DOTALL | re.IGNORECASE,
)
SQL_SET_OPERATION_RE = re.compile(r"\b(?:UNION(?:\s+ALL)?|INTERSECT|EXCEPT)\s+SELECT\b", re.IGNORECASE)
SQL_JSON_QUERY_RE = re.compile(
    rf"SELECT\b.*\bFROM\b.*\bWHERE\s+{SQL_COLUMN_PATTERN}\s*{SQL_JSON_OPERATOR_PATTERN}\s*{SQL_VALUE_PATTERN}",
    re.DOTALL | re.IGNORECASE,
)
SQL_PROSE_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?:the|an|this|that|these|those|because|please|you|your|we|they|"
    r"should|could|will|must|continue|continuing|before|after)"
    r"(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
SQL_RETURNING_PROSE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?:the|because|please|you|your|we|they|"
    r"should|could|will|must|then|continue|continuing|and|but)"
    r"(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
SQL_ARTICLE_PROSE_RE = re.compile(r"^\s*a\s+[A-Za-z]+\b", re.IGNORECASE)
TS_TYPE_DIAGNOSTIC_RE = re.compile(
    r"""^Type ['\"][^'\"]+['\"] (?:is not assignable(?: to type)?|has no property|does not satisfy the constraint|is missing the following properties|has no properties in common with type|cannot be used as an index type|does not have any construct or call signatures|is incompatible with type|is not comparable(?: to type)?|has no call signatures)\b""",
    re.IGNORECASE,
)
TS_DIAGNOSTIC_RE = re.compile(
    r"^(?:Property ['\"][^'\"]+['\"] does not exist on type ['\"][^'\"]+['\"]|"
    r"Property ['\"][^'\"]+['\"] is missing in type .+ but required in type .+|"
    r"Argument of type ['\"][^'\"]+['\"] is not assignable to (?:parameter|argument) of type |"
    r"Cannot assign to ['\"][^'\"]+['\"] because it is a read-only property|"
    r"Variable ['\"][^'\"]+['\"] implicitly has an ['\"]any['\"] type|"
    r"This comparison appears to be unintentional because .+|"
    r"Operator ['\"][^'\"]+['\"] cannot be applied to types .+|"
    r"Cannot find module ['\"][^'\"]+['\"]|No overload matches this call|"
    r"Object literal may only specify known properties, and .+ does not exist in type .+|"
    r"Element implicitly has an ['\"]any['\"] type because expression of type .+ can't be used to index type .+|"
    r"Module .+ has no exported member .+)",
    re.IGNORECASE,
)
JS_ASSERTION_METHODS = {
    "toBe",
    "toEqual",
    "toStrictEqual",
    "toHaveProperty",
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


def _is_sql_value(value: str) -> bool:
    sql_text = re.sub(r"'[^']*'|\"[^\"]*\"", " ", value)
    sql_text = re.sub(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*\b", " ", sql_text)
    sql_text = re.sub(
        r"\b(?:FROM|JOIN)\s+([\w.]+)\s+"
        r"(?!(?:UNION|INTERSECT|EXCEPT|WHERE|ORDER|GROUP|LIMIT|OFFSET|HAVING|RETURNING|ON|JOIN|LEFT|RIGHT|FULL|INNER|CROSS)\b)[A-Za-z_]\w*\b",
        r"\1",
        sql_text,
        flags=re.IGNORECASE,
    )
    if SQL_UPDATE_RE.fullmatch(value) or SQL_DELETE_RE.fullmatch(value) or SQL_INSERT_RE.fullmatch(value):
        returning = re.search(r"\bRETURNING\b(.*)$", sql_text, re.IGNORECASE)
        return returning is None or not SQL_RETURNING_PROSE_RE.search(returning.group(1))
    if SQL_PROSE_WORD_RE.search(sql_text):
        return False
    if SQL_WITH_QUERY_RE.fullmatch(sql_text) or SQL_JSON_QUERY_RE.fullmatch(value):
        return True
    select = SQL_SELECT_PREFIX_RE.match(value)
    if select is not None:
        projection = select.group(1).strip()
        structured_projection = "," in projection or projection == "*"
        remainder = value[select.end() :].strip()
        simple_alias = re.fullmatch(r"(?:AS\s+)?[A-Za-z]\b", remainder, re.IGNORECASE)
        if SQL_SET_OPERATION_RE.search(sql_text):
            return True
        if (
            not SQL_ARTICLE_PROSE_RE.match(projection)
            and (
                SQL_QUERY_RE.fullmatch(value)
                or (structured_projection and (not remainder or simple_alias is not None))
            )
        ):
            return True
    return bool(SQL_WHERE_RE.fullmatch(value))


def _is_structured_key_value(value: str) -> bool:
    matches = list(KEY_VALUE_RE.finditer(value))
    if KEY_VALUE_RE.fullmatch(value):
        return True
    if len(matches) < 2 or HAN_RE.search(value):
        return False
    remaining = KEY_VALUE_RE.sub(" ", value)
    return len(WORD_RE.findall(remaining)) <= 2 * len(matches)


def _is_structured_permission_list(value: str) -> bool:
    entries = value.split(",")
    if len(entries) < 2:
        return False
    for entry in entries:
        if ":" not in entry:
            return False
        label, level = entry.split(":", 1)
        if label.strip().lower() not in STRUCTURED_PERMISSION_RESOURCES:
            return False
        if level.strip().lower() not in STRUCTURED_PERMISSION_LEVELS:
            return False
    return True


def _is_route_value(value: str) -> bool:
    return (value.startswith("?") and KEY_VALUE_RE.search(value) is not None) or (
        value.startswith("#/") and not any(char.isspace() for char in value)
    )


def _is_utility_class_list(value: str) -> bool:
    tokens = value.split()
    if len(tokens) < 5 or not all(UTILITY_CLASS_TOKEN_RE.fullmatch(token) for token in tokens):
        return False
    recognized = sum(
        token in UTILITY_CLASS_SINGLETONS or token.startswith(UTILITY_CLASS_PREFIXES) for token in tokens
    )
    return recognized >= max(3, (len(tokens) * 3 + 3) // 4)


def _is_explanatory_sentence(value: str) -> bool:
    value = " ".join(value.split())
    han_count = len(HAN_RE.findall(value))
    if (
        not value
        or PATH_VALUE_RE.fullmatch(value)
        or ABSOLUTE_PATH_VALUE_RE.fullmatch(value)
        or _is_route_value(value)
        or _is_utility_class_list(value)
        or EMAIL_RE.fullmatch(value)
        or COMMIT_TITLE_RE.match(value)
        or _is_structured_key_value(value)
        or STRUCTURED_LABEL_LIST_RE.fullmatch(value)
        or _is_structured_permission_list(value)
        or SHELL_COMMAND_RE.search(value)
        or WORKFLOW_EXPRESSION_RE.fullmatch(value)
        or value.startswith(
            (
                "<", ":::", "import ", "src/", "backend/", "frontend/", "deploy/", "可递：",
                "missing:", "since=", "Co-authored-by:", "::error::", "## ",
            )
        )
        or value.count("|") >= 2
        or ":::" in value
        or STEP_DIAGNOSTIC_RE.fullmatch(value)
        or re.fullmatch(r"step\s+[A-Za-z0-9_.:-]+", value, re.IGNORECASE)
        or SQL_EXPRESSION_RE.fullmatch(value)
        or _is_sql_value(value)
        or TS_TYPE_DIAGNOSTIC_RE.match(value)
        or TS_DIAGNOSTIC_RE.match(value)
        or (EMAIL_RE.search(value) and not han_count and len(WORD_RE.findall(value)) < 9)
        or IDENTIFIER_RE.fullmatch(value)
        or STATUS_CODES_RE.fullmatch(value)
    ):
        return False
    if han_count >= 18:
        return True
    words = WORD_RE.findall(value)
    return len(value) >= 40 and len(words) >= 7


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
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        SCAN_ERRORS.append(f"{path}: 无法扫描测试文件: {exc}")
        return []
    literals: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            seen_candidates: set[int] = set()
            for operator, comparator in zip(node.ops, node.comparators):
                if isinstance(operator, ast.Eq):
                    for candidate in (left, comparator):
                        if id(candidate) in seen_candidates:
                            continue
                        seen_candidates.add(id(candidate))
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


def _call_argument_strings(
    tokens: list[tuple[str, int]], open_index: int, *, skip_first: bool = False
) -> list[tuple[int, str]]:
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
    for index, argument in enumerate(arguments[:2]):
        if skip_first and index == 0:
            continue
        while argument:
            if [token for token, _ in argument[-2:]] == ["as", "const"]:
                argument = argument[:-2]
                continue
            if len(argument) >= 2 and argument[0][0] == "(" and argument[-1][0] == ")":
                nesting = 0
                wraps_all = True
                for index, (token, _) in enumerate(argument):
                    if token == "(":
                        nesting += 1
                    elif token == ")":
                        nesting -= 1
                        if nesting == 0 and index != len(argument) - 1:
                            wraps_all = False
                            break
                if wraps_all:
                    argument = argument[1:-1]
                    continue
            break

        nested: list[bool] = []
        for index, (token, line) in enumerate(argument):
            if token in {"(", "[", "{"}:
                previous = argument[index - 1][0] if index else ""
                is_call = token == "(" and (
                    bool(IDENTIFIER_RE.fullmatch(previous)) or previous in {")", "]", "?.", "!"}
                )
                is_subscript = token == "[" and index > 0 and (
                    bool(IDENTIFIER_RE.fullmatch(previous)) or previous in {")", "]", "?.", "!"}
                )
                nested.append(any(nested) or is_call or is_subscript)
            elif token in {")", "]", "}"}:
                if nested:
                    nested.pop()
            elif _is_js_string(token) and not any(nested):
                literals.append((line, _decode_js_string(token)))
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
        source = path.read_text(encoding="utf-8-sig")
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
            literals.extend(
                _call_argument_strings(tokens, index + 1, skip_first=token == "toHaveProperty")
            )
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
