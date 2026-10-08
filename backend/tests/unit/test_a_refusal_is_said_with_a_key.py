"""A refusal is said with ``say()``, never written out where it is raised.

An error raised with a ``say()`` sentence goes out with its key beside the
Chinese ``message`` (``error.i18n``), and the reader's screen renders it in the
language they picked (``docs/i18n.md``). A sentence written into the call
instead — Chinese or English — carries no key, so the screen falls back to the
server's own words, in whatever language they were written in. That is how an
English refusal reached a Chinese reader, and nothing fails when one is
written. This scan makes it fail.

An exception constructor here is the call a ``raise`` raises, ``HTTPException``,
any call to a class named ``*Error`` / ``*Exception``, and any class in the app
that inherits from one of those. Two things it may not hold outside a
``say(...)``:

- Chinese text: a literal, an f-string, a concatenation, a ``.format()``, at
  any depth of the call's arguments.
- an English sentence: the same, but only where the string is an argument of
  the raise itself and not a value inside a container passed to it —
  ``data={"type": "team"}`` holds values, and a value is not a sentence. A
  string counts as a sentence when it holds whitespace; a single token
  (``utf-8``, ``task``, ``CONSENT_REQUIRED``) is left alone, and the scan reads
  the two apart by nothing more than that.

The one way out is a comment, ``# i18n-exempt: <why>``, at the end of the line
of the text or of the call, or alone on the line just above the call (a reason
seldom fits beside the code within the line length). One without a reason
fails too.

The English sentences already written are counted per file in ``_BASELINE``
below and may only go down, the app's own idiom for a debt paid a piece at a
time (``test_legacy_error_ratchet.py``). This task converts the defaults and
the way a missing project answers; the rest of the backlog shows up there as
counts to lower.
"""

import ast
import functools
import io
import re
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
APP = BACKEND / "app"

CJK = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")
EXEMPT = re.compile(r"#\s*i18n-exempt\b(?P<rest>.*)")
#: The escapes that can spell a CJK character inside a string literal.
ESCAPE = re.compile(r"\\(u|U|N\{)")

#: Calls whose arguments are a sentence's parameters, not its text.
SAYING = {"say", "listing"}

#: Whitespace, which is what tells a written sentence from a written token.
PHRASE = re.compile(r"\s")

#: What a value is carried in: inside one of these, a string is a value.
CONTAINERS = (
    ast.Dict,
    ast.List,
    ast.Set,
    ast.Tuple,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
)

HOW_TO_FIX = (
    "A refusal sentence is written where it is raised. Add it to both "
    "frontend/src/i18n/messages/zh-CN/apiError.json and "
    "frontend/src/i18n/messages/en/apiError.json under one key, and raise "
    "say('<key>', **params) instead (app/core/sentences.py; "
    "docs/i18n.md). If this text truly is not shown to a person, put "
    "'# i18n-exempt: <reason>' on its line."
)

ENGLISH_TO_FIX = (
    "An English sentence is written where a refusal is raised, so a reader on "
    "the Chinese UI is shown it as it is. Raise say('<key>', **params) "
    "instead, with the sentence in both catalogs (app/core/sentences.py; "
    "docs/i18n.md), or put '# i18n-exempt: <reason>' on its line."
)


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_error_name(name: str | None) -> bool:
    return name is not None and (
        name.endswith(("Error", "Exception")) or name == "BaseException"
    )


def _error_classes(trees: dict[Path, ast.Module]) -> set[str]:
    """Classes in the app that inherit, at any depth, from an exception."""
    bases: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    n for b in node.bases if (n := _name(b))
                )
    errors = {name for name in bases if _is_error_name(name)}
    grew = True
    while grew:
        found = {
            name
            for name, parents in bases.items()
            if name not in errors
            and any(_is_error_name(p) or p in errors for p in parents)
        }
        errors |= found
        grew = bool(found)
    return errors


def _cjk_nodes(node: ast.AST) -> list[ast.Constant]:
    """Chinese string constants in ``node``, outside any ``say(...)``."""
    if isinstance(node, ast.Call) and _name(node.func) in SAYING:
        return []
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str) and CJK.search(node.value):
            return [node]
        return []
    return [hit for child in ast.iter_child_nodes(node) for hit in _cjk_nodes(child)]


def _phrase_nodes(node: ast.AST, *, value: bool = False) -> list[ast.Constant]:
    """English sentences in ``node``, outside any ``say(...)``.

    ``value`` says a container has been entered on the way here, so what is
    left is the contents of a value rather than the raise's own argument.
    """
    if isinstance(node, ast.Call) and _name(node.func) in SAYING:
        return []
    if isinstance(node, ast.Constant):
        if value or not isinstance(node.value, str) or not PHRASE.search(node.value):
            return []
        return [node]
    holds_a_value = value or isinstance(node, CONTAINERS)
    return [
        hit
        for child in ast.iter_child_nodes(node)
        for hit in _phrase_nodes(child, value=holds_a_value)
    ]


def _constructors(tree: ast.Module, errors: set[str]) -> list[ast.Call]:
    calls: dict[int, ast.Call] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) not in SAYING
        ):
            calls[id(node.exc)] = node.exc
        elif isinstance(node, ast.Call):
            name = _name(node.func)
            if name == "HTTPException" or _is_error_name(name) or name in errors:
                calls[id(node)] = node
    return list(calls.values())


def _comments(source: str) -> dict[int, tuple[str, bool]]:
    """line -> (comment, whether it is alone on its line)."""
    found = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            alone = token.line.strip().startswith("#")
            found[token.start[0]] = (token.string, alone)
    return found


def scan(paths: list[Path]) -> tuple[list[str], Counter[str], list[str]]:
    """(Chinese sentences raised, English sentences per file, unexplained exemptions).

    A Chinese sentence is one ``file:line: <the line>`` entry; an English one is
    counted per file, because the app holds a thousand of them and they are paid
    off a file at a time (``_BASELINE``).
    """
    sources = {path: path.read_text("utf-8") for path in paths}
    trees = {path: ast.parse(source) for path, source in sources.items()}
    errors = _error_classes(trees)
    violations, unexplained = [], []
    english: Counter[str] = Counter()
    for path, tree in trees.items():
        where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        # The baseline names a file as everything under backend/ does.
        named = path.relative_to(BACKEND) if path.is_relative_to(BACKEND) else path
        source = sources[path]
        # Tokenizing is the costly half of reading a file, and only a file that
        # spells the marker can hold an exemption.
        comments = _comments(source) if "i18n-exempt" in source else {}
        for line, (comment, _) in comments.items():
            exempt = EXEMPT.search(comment)
            if exempt and not exempt["rest"].lstrip(" :").strip():
                unexplained.append(f"{where}:{line}: {comment.strip()}")
        # A comment alone on its line exempts the line below it.
        exempt_lines = {
            line + alone
            for line, (comment, alone) in comments.items()
            if EXEMPT.search(comment)
        }
        lines = source.splitlines()
        chinese: set[int] = set()
        sentences: set[int] = set()
        for call in _constructors(tree, errors):
            arguments = [*call.args, *(k.value for k in call.keywords)]
            exempted = bool({call.lineno} & exempt_lines)
            for hit in (h for a in arguments for h in _cjk_nodes(a)):
                if exempted or hit.lineno in exempt_lines:
                    continue
                violations.append(
                    f"{where}:{hit.lineno}: {lines[hit.lineno - 1].strip()}"
                )
                chinese.add(hit.lineno)
            for hit in (h for a in arguments for h in _phrase_nodes(a)):
                if exempted or hit.lineno in exempt_lines:
                    continue
                sentences.add(hit.lineno)
        # A line already reported in Chinese is one debt, not two.
        sentences -= chinese
        if sentences:
            english[str(named)] += len(sentences)
    return sorted(set(violations)), english, unexplained


#: The English sentences a refusal still carries, per file, as they stand. A
#: count that grew is a new one: raise it with ``say()``. A count that shrank is
#: one converted: lower or delete its line here. The file is the list — adding
#: to it goes through review, as ``test_legacy_error_ratchet.py`` puts it.
_BASELINE: dict[str, int] = {
    "app/api/auth.py": 11,
    "app/api/preview_host.py": 4,
    "app/api/preview_owner_internal.py": 8,
    "app/api/routes/addresses.py": 2,
    "app/api/routes/admin_run_records.py": 1,
    "app/api/routes/agent_control.py": 5,
    "app/api/routes/agent_credential.py": 1,
    "app/api/routes/alerts.py": 1,
    "app/api/routes/answers.py": 8,
    "app/api/routes/assistant.py": 1,
    "app/api/routes/attachments.py": 4,
    "app/api/routes/avatars.py": 5,
    "app/api/routes/backend_log.py": 2,
    "app/api/routes/blocks.py": 3,
    "app/api/routes/comments.py": 3,
    "app/api/routes/connector.py": 1,
    "app/api/routes/dashboard.py": 5,
    "app/api/routes/docs_site.py": 4,
    "app/api/routes/document_agent.py": 1,
    "app/api/routes/execution.py": 11,
    "app/api/routes/fetch.py": 3,
    "app/api/routes/forge_token.py": 2,
    "app/api/routes/git_http.py": 2,
    "app/api/routes/github_account_link.py": 1,
    "app/api/routes/groups.py": 4,
    "app/api/routes/knowledge.py": 7,
    "app/api/routes/legal.py": 2,
    "app/api/routes/living_docs.py": 1,
    "app/api/routes/llm_proxy.py": 7,
    "app/api/routes/materialbundles.py": 2,
    "app/api/routes/materials.py": 3,
    "app/api/routes/notifications_flat.py": 6,
    "app/api/routes/notifications_live.py": 1,
    "app/api/routes/preview_sessions.py": 3,
    "app/api/routes/project_channels.py": 2,
    "app/api/routes/project_context.py": 1,
    "app/api/routes/project_environment.py": 3,
    "app/api/routes/project_skills.py": 1,
    "app/api/routes/projects.py": 7,
    "app/api/routes/projects_run_config.py": 3,
    "app/api/routes/questions.py": 11,
    "app/api/routes/recruitment.py": 2,
    "app/api/routes/remote_mcp.py": 6,
    "app/api/routes/routines.py": 2,
    "app/api/routes/run_records.py": 1,
    "app/api/routes/sandbox.py": 1,
    "app/api/routes/skill_bundles.py": 3,
    "app/api/routes/space_announcements.py": 1,
    "app/api/routes/spaces.py": 13,
    "app/api/routes/spaces_analytics.py": 2,
    "app/api/routes/spaces_organization.py": 2,
    "app/api/routes/tags.py": 1,
    "app/api/routes/task_inheritance.py": 1,
    "app/api/routes/tasks/_common.py": 11,
    "app/api/routes/tasks/lifecycle.py": 29,
    "app/api/routes/tasks/participation.py": 25,
    "app/api/routes/tasks/publish_pdf.py": 8,
    "app/api/routes/tasks/roster.py": 12,
    "app/api/routes/tasks/submissions.py": 15,
    "app/api/routes/teams.py": 8,
    "app/api/routes/topic_members.py": 2,
    "app/api/routes/topics.py": 9,
    "app/api/routes/topics_channel.py": 2,
    "app/api/routes/topics_compute.py": 2,
    "app/api/routes/topics_messages.py": 5,
    "app/api/routes/topics_pins.py": 2,
    "app/api/routes/topics_preview.py": 1,
    "app/api/routes/topics_side_routes.py": 1,
    "app/api/routes/topics_threads.py": 2,
    "app/api/routes/users/_common.py": 8,
    "app/api/routes/users/account.py": 11,
    "app/api/routes/users/auth.py": 31,
    "app/api/routes/users/connections.py": 3,
    "app/api/routes/users/directory.py": 5,
    "app/api/routes/users/oauth.py": 4,
    "app/api/routes/users/preferences.py": 2,
    "app/api/routes/users/registration.py": 8,
    "app/api/routes/users_2fa.py": 9,
    "app/api/routes/users_common.py": 3,
    "app/api/routes/users_identity.py": 5,
    "app/api/routes/users_language.py": 2,
    "app/api/routes/users_passkey.py": 10,
    "app/api/routes/users_password.py": 3,
    "app/api/routes/users_sessions.py": 1,
    "app/api/routes/users_timezone.py": 2,
    "app/api/routes/webhooks.py": 2,
    "app/api/routes/workspace.py": 7,
    "app/api/task_teaching.py": 2,
    "app/api/write_access.py": 3,
    "app/auth/checker.py": 2,
    "app/common/auth.py": 4,
    "app/core/config.py": 16,
    "app/core/crypto.py": 1,
    "app/core/db.py": 1,
    "app/core/forge_events.py": 2,
    "app/core/sandbox_auth.py": 2,
    "app/core/sentences.py": 3,
    "app/core/single_use_state.py": 4,
    "app/core/storage.py": 1,
    "app/device_connection_app.py": 8,
    "app/domain/agent/announce.py": 1,
    "app/domain/agent/chat.py": 10,
    "app/domain/agent/cli_worker.py": 6,
    "app/domain/agent/clone.py": 1,
    "app/domain/agent/compute.py": 1,
    "app/domain/agent/device_hub.py": 5,
    "app/domain/agent/device_hub_rpc.py": 2,
    "app/domain/agent/device_provider.py": 4,
    "app/domain/agent/device_storage.py": 4,
    "app/domain/agent/document/box.py": 1,
    "app/domain/agent/document/thread.py": 1,
    "app/domain/agent/environment_runner.py": 9,
    "app/domain/agent/executor_transport.py": 22,
    "app/domain/agent/forge_cli.py": 10,
    "app/domain/agent/forgejo_tokens.py": 7,
    "app/domain/agent/gateway.py": 2,
    "app/domain/agent/gateway_usage.py": 2,
    "app/domain/agent/github_app.py": 5,
    "app/domain/agent/harness/claude_code/legacy.py": 6,
    "app/domain/agent/harness/claude_code/remote_execution/bootstrap.py": 18,
    "app/domain/agent/harness/claude_code/remote_execution/client.py": 17,
    "app/domain/agent/harness/claude_code/remote_execution/context_service.py": 1,
    "app/domain/agent/harness/claude_code/remote_execution/forwarded_fs.py": 2,
    "app/domain/agent/harness/claude_code/remote_execution/machine_files.py": 1,
    "app/domain/agent/harness/claude_code/remote_execution/predecessor.py": 3,
    "app/domain/agent/harness/claude_code/remote_execution/private.py": 12,
    "app/domain/agent/harness/claude_code/remote_execution/private_egress.py": 4,
    "app/domain/agent/harness/claude_code/remote_execution/release.py": 3,
    "app/domain/agent/harness/claude_code/remote_execution/runtime.py": 40,
    "app/domain/agent/harness/claude_code/remote_execution/sandbox_host.py": 14,
    "app/domain/agent/harness/claude_code/remote_execution/session_transfer.py": 9,
    "app/domain/agent/harness/claude_code/runner.py": 7,
    "app/domain/agent/harness/claude_code/subscription.py": 7,
    "app/domain/agent/harness/codex/app_server.py": 2,
    "app/domain/agent/harness/codex/host.py": 5,
    "app/domain/agent/harness/codex/runner.py": 4,
    "app/domain/agent/harness/codex/session.py": 2,
    "app/domain/agent/harness/codex/subscription.py": 1,
    "app/domain/agent/harness/codex/tools.py": 4,
    "app/domain/agent/harness/driven/runner.py": 3,
    "app/domain/agent/harness/driven/subscription.py": 3,
    "app/domain/agent/harness/pi/hooks.py": 1,
    "app/domain/agent/harness/pi/host.py": 4,
    "app/domain/agent/harness/pi/jobs.py": 1,
    "app/domain/agent/harness/pi/machine.py": 1,
    "app/domain/agent/harness/pi/mcp.py": 1,
    "app/domain/agent/harness/pi/rpc.py": 2,
    "app/domain/agent/harness/pi/runner.py": 4,
    "app/domain/agent/harness/pi/subagents.py": 2,
    "app/domain/agent/initial_admission.py": 1,
    "app/domain/agent/input_registration.py": 1,
    "app/domain/agent/machine_tunnel.py": 7,
    "app/domain/agent/memory_ledger.py": 2,
    "app/domain/agent/preview_hub.py": 3,
    "app/domain/agent/preview_owner.py": 1,
    "app/domain/agent/preview_tunnel.py": 20,
    "app/domain/agent/private_chat.py": 2,
    "app/domain/agent/profiles.py": 1,
    "app/domain/agent/project_hooks.py": 4,
    "app/domain/agent/queries.py": 4,
    "app/domain/agent/resource_cleanup.py": 23,
    "app/domain/agent/room/sessions.py": 3,
    "app/domain/agent/room/turn.py": 1,
    "app/domain/agent/session_host/claude_code.py": 3,
    "app/domain/agent/session_host/consumptions.py": 4,
    "app/domain/agent/session_host/driver.py": 1,
    "app/domain/agent/session_host/host.py": 11,
    "app/domain/agent_credential/services.py": 1,
    "app/domain/agent_instance/services.py": 1,
    "app/domain/agent_type/library.py": 11,
    "app/domain/answers/services.py": 10,
    "app/domain/attachment/services.py": 5,
    "app/domain/avatars/services.py": 3,
    "app/domain/block/documents.py": 1,
    "app/domain/block/editing.py": 3,
    "app/domain/comments/services.py": 7,
    "app/domain/dashboard/services.py": 3,
    "app/domain/delivery/agent.py": 3,
    "app/domain/delivery/receipts.py": 12,
    "app/domain/delivery/timer.py": 2,
    "app/domain/device/service.py": 9,
    "app/domain/discussion/reaction_services.py": 2,
    "app/domain/discussion/services.py": 6,
    "app/domain/documents/editor.py": 1,
    "app/domain/feedback/repositories.py": 1,
    "app/domain/fetch/addresses.py": 2,
    "app/domain/fetch/guard.py": 6,
    "app/domain/frontend_log.py": 1,
    "app/domain/groups/services.py": 26,
    "app/domain/knowledge/repositories.py": 1,
    "app/domain/knowledge/services.py": 8,
    "app/domain/library/blobs.py": 2,
    "app/domain/library/service.py": 3,
    "app/domain/living_doc/collab.py": 2,
    "app/domain/living_doc/comment_schemas.py": 1,
    "app/domain/living_doc/schemas.py": 3,
    "app/domain/machine/claude_dist.py": 7,
    "app/domain/machine/enrollment.py": 7,
    "app/domain/machine/lifecycle.py": 1,
    "app/domain/machine/microcloud.py": 3,
    "app/domain/machine/pi_dist.py": 6,
    "app/domain/machine/services.py": 7,
    "app/domain/machine/session_work.py": 14,
    "app/domain/machine/supply.py": 2,
    "app/domain/machine/toolchain_dist.py": 4,
    "app/domain/machine/warm.py": 1,
    "app/domain/materials/services.py": 12,
    "app/domain/membership/services.py": 6,
    "app/domain/notification/maintenance.py": 2,
    "app/domain/notification/outbox.py": 1,
    "app/domain/notification/preferences.py": 2,
    "app/domain/notification/push_delivery.py": 1,
    "app/domain/notification/services.py": 2,
    "app/domain/oauth/services.py": 5,
    "app/domain/passkey/services.py": 5,
    "app/domain/project/environment.py": 2,
    "app/domain/project/export.py": 13,
    "app/domain/project/forge_migration.py": 20,
    "app/domain/project/room_files.py": 1,
    "app/domain/project/services.py": 1,
    "app/domain/project_skill/blobs.py": 1,
    "app/domain/questions/services.py": 31,
    "app/domain/repository/service.py": 2,
    "app/domain/review/events.py": 1,
    "app/domain/review/github_pr.py": 11,
    "app/domain/review/pr_publish.py": 5,
    "app/domain/review/services/cards.py": 2,
    "app/domain/room_task/checkouts.py": 1,
    "app/domain/room_task/services.py": 1,
    "app/domain/site/services.py": 2,
    "app/domain/space/analytics_service.py": 1,
    "app/domain/space/analytics_view_service.py": 10,
    "app/domain/space/announcement_service.py": 5,
    "app/domain/space/material_service.py": 9,
    "app/domain/space/member_publishing_service.py": 6,
    "app/domain/space/review_service.py": 7,
    "app/domain/space/services.py": 59,
    "app/domain/tag/services.py": 2,
    "app/domain/task/access.py": 1,
    "app/domain/task/attachment_service.py": 10,
    "app/domain/task/inputs.py": 7,
    "app/domain/task/services.py": 17,
    "app/domain/task/task_pdf_draft_service.py": 13,
    "app/domain/team/membership_services.py": 15,
    "app/domain/team/recruitment_services.py": 6,
    "app/domain/team/repositories.py": 1,
    "app/domain/team/services.py": 28,
    "app/domain/team/vocabulary.py": 1,
    "app/domain/textfile.py": 1,
    "app/domain/thread/services.py": 1,
    "app/domain/topic/cleanup_trigger.py": 1,
    "app/domain/topic/retire.py": 9,
    "app/domain/topic/services.py": 15,
    "app/domain/topic_membership/services.py": 10,
    "app/domain/usage/ledger.py": 1,
    "app/domain/user/mail_quota.py": 1,
    "app/domain/user/passwords.py": 2,
    "app/domain/user/realname_services.py": 4,
    "app/domain/user/services.py": 6,
    "app/domain/user/verification_service.py": 2,
    "app/forge_events_app.py": 4,
}


@functools.cache
def _app_scan() -> tuple[list[str], Counter[str], list[str]]:
    """The scan of the whole app, once per process for the tests below."""
    return scan(sorted(APP.rglob("*.py")))


def test_no_error_is_raised_with_chinese_written_into_it():
    violations, _, _ = _app_scan()
    assert violations == [], HOW_TO_FIX + "\n" + "\n".join(violations)


def test_every_exemption_says_why():
    _, _, unexplained = _app_scan()
    assert unexplained == [], (
        "An i18n exemption needs its reason on the same line: "
        "'# i18n-exempt: <why this text is not shown to a person>'.\n"
        + "\n".join(unexplained)
    )


def test_a_refusal_isnt_written_in_english_or_the_count_only_falls():
    _, found, _ = _app_scan()
    grew = {
        path: (count, _BASELINE.get(path, 0))
        for path, count in found.items()
        if count > _BASELINE.get(path, 0)
    }
    assert not grew, (
        "English written where a refusal is raised, more than this file held "
        f"before (file: now, before): {grew}\n{ENGLISH_TO_FIX}"
    )
    shrank = {
        path: (count, found.get(path, 0))
        for path, count in _BASELINE.items()
        if found.get(path, 0) < count
    }
    assert not shrank, (
        "These English refusals are gone; lower or delete their lines in "
        f"_BASELINE (file: before, now): {shrank}"
    )


def _scan_text(
    tmp_path: Path, source: str
) -> tuple[list[str], Counter[str], list[str]]:
    path = tmp_path / "sample.py"
    path.write_text(source, "utf-8")
    return scan([path])


def test_the_scan_catches_each_way_chinese_reaches_an_error(tmp_path):
    source = """
from fastapi import HTTPException
from app.core.errors import BaseError, ConflictError

class Refused(BaseError):
    pass

class Worse(Refused):
    pass

def f(name, say):
    raise ConflictError("已经有了")
    raise ConflictError(f"{name} 已经有了")
    raise ConflictError("已经" + name)
    raise ConflictError("{} 已经有了".format(name))
    raise HTTPException(status_code=409, detail="已经有了")
    raise ValueError("不对")
    error = Worse("不行")
    raise Exception("不行")
"""
    violations, _, _ = _scan_text(tmp_path, source)
    assert [v.split(":")[1] for v in violations] == [
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
    ]


def test_a_say_sentence_a_token_and_a_value_pass(tmp_path):
    source = """
from app.core.errors import ConflictError
from app.core.sentences import listing, say

def f(names):
    raise ConflictError(say("inviteSelf"))
    raise ConflictError(say("soleTopicOwner", topics=listing(["话题"], quoted=True)))
    raise ConflictError(say("inviteSelf"), data={"type": "team"})
    raise ConflictError("utf-8")
    print("不是错误")
"""
    assert _scan_text(tmp_path, source) == ([], {}, [])


def test_an_exemption_needs_a_reason(tmp_path):
    source = """
def f():
    raise ValueError("不对")  # i18n-exempt: matched by the upstream CLI parser
    raise ValueError("不对")  # i18n-exempt
    raise ValueError("不对")  # i18n-exempt:
    # i18n-exempt: runs where the catalog is not shipped
    raise ValueError("不对")
    # i18n-exempt: only the line just below is exempt
    x = 1
    raise ValueError("不对")
"""
    violations, _, unexplained = _scan_text(tmp_path, source)
    assert [v.split(":")[1] for v in violations] == ["10"]
    assert [u.split(":")[1] for u in unexplained] == ["4", "5"]


def test_the_scan_catches_english_written_into_a_refusal(tmp_path):
    source = """
from fastapi import HTTPException
from app.core.errors import ConflictError, NotFoundError

def f(name):
    raise NotFoundError("Project not found")
    raise NotFoundError(f"Resource {name} not found")
    raise ConflictError("Resource " + name + " not found")
    raise HTTPException(status_code=404, detail="Project not found")
    raise NotFoundError("utf-8")
    raise ConflictError("already there", data={"type": "team"})
    print("not a refusal")
"""
    violations, counts, _ = _scan_text(tmp_path, source)
    assert violations == []
    # The five sentences are the debts; "utf-8" is a token a call site passes
    # and "team" is the value of a key, so neither is a sentence. The sample
    # stands outside backend/, so the file is named by its whole path.
    assert list(counts.values()) == [5]


def test_an_exemption_covers_english_too(tmp_path):
    source = """
from app.core.errors import ConflictError

def f():
    raise ConflictError("already there")  # i18n-exempt: the CLI prints this
"""
    assert _scan_text(tmp_path, source) == ([], {}, [])
