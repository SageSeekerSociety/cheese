"""Ratchet: the deprecated AppError family only ever shrinks.

AppError, ValidationError, UnauthorizedError and GatewayUnavailableError
(``app/core/errors.py``) are each already the BaseError class replacing them;
what is left is moving their uses over, domain by domain, and then deleting
them. This test keeps that one-way: every use of the four names is counted per
file, and the counts are written down below, in code rather than in a config
file, so that adding to them goes through review.

It is red both ways. A count that grew is a new use: raise the replacement
named in the class's docstring instead. A count that shrank is a debt paid:
lower or delete its line here. When ``_BASELINE`` is empty the four classes go.

A use is any reference to one of the names as imported from
``app.core.errors`` (``as`` renames and ``errors.X`` included), so pydantic's
own ValidationError never counts. A name re-exported through another module
and ``import *`` are not followed; neither occurs today. Only the four names
count: subclassing OverTier or raising ForgeUnreachableError (both AppErrors
underneath) does not, so those subclasses go the same way as their base.
"""

import ast
import re
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

LEGACY = frozenset(
    {"AppError", "ValidationError", "UnauthorizedError", "GatewayUnavailableError"}
)
_MODULE = "app.core.errors"

#: The only spellings that can bind the names; a file without one is skipped
#: before it is parsed, which is what keeps this inside a pure test's ceiling.
_MENTIONS_MODULE = re.compile(
    r"app\.core\.errors|from app\.core import [^\n]*\berrors\b"
)

#: Where the classes live, and this file: neither is a use.
_SKIPPED = frozenset({"app/core/errors.py", "tests/unit/test_legacy_error_ratchet.py"})


def _uses(tree: ast.AST) -> Counter[str]:
    # One walk that tallies every name and every `module.attr`, resolved
    # against the imports afterwards: imports inside functions are common
    # here, so a use can be walked before the import that binds it.
    names: dict[str, str] = {}  # local binding -> legacy name
    modules: set[str] = set()  # local bindings of app.core.errors itself
    bare: Counter[str] = Counter()
    dotted: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            bare[node.id] += 1
        elif isinstance(node, ast.Attribute):
            if node.attr in LEGACY and isinstance(node.value, ast.Name):
                dotted[(node.value.id, node.attr)] += 1
        elif isinstance(node, ast.ImportFrom):
            if node.module == _MODULE:
                for alias in node.names:
                    if alias.name in LEGACY:
                        names[alias.asname or alias.name] = alias.name
            elif node.module == "app.core":
                modules.update(
                    a.asname or a.name for a in node.names if a.name == "errors"
                )
        elif isinstance(node, ast.Import):
            modules.update(
                a.asname for a in node.names if a.name == _MODULE and a.asname
            )
    found: Counter[str] = Counter()
    for local, legacy in names.items():
        if bare[local]:
            found[legacy] += bare[local]
    for (module, attr), count in dotted.items():
        if module in modules:
            found[attr] += count
    return found


def _scan() -> dict[tuple[str, str], int]:
    found: dict[tuple[str, str], int] = {}
    for tree_root in ("app", "tests"):
        for path in sorted((BACKEND_ROOT / tree_root).rglob("*.py")):
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            if rel in _SKIPPED:
                continue
            text = path.read_text()
            # Parsing both trees whole takes longer than a pure test may; a
            # file that never spells one of the names cannot use it.
            if not _MENTIONS_MODULE.search(text) or not any(n in text for n in LEGACY):
                continue
            for name, count in _uses(ast.parse(text)).items():
                found[(rel, name)] = count
    return found


# (file, name) -> uses. Only ever lowered or deleted.
_BASELINE: dict[tuple[str, str], int] = {
    ("app/api/doc_access.py", "ValidationError"): 2,
    ("app/api/preview_host.py", "AppError"): 3,
    ("app/api/routes/addresses.py", "ValidationError"): 2,
    ("app/api/routes/admin_models.py", "GatewayUnavailableError"): 1,
    ("app/api/routes/agent_control.py", "ValidationError"): 1,
    ("app/api/routes/alerts.py", "ValidationError"): 2,
    ("app/api/routes/backend_log.py", "UnauthorizedError"): 1,
    ("app/api/routes/connector.py", "UnauthorizedError"): 3,
    ("app/api/routes/dashboard.py", "ValidationError"): 1,
    ("app/api/routes/docs_site.py", "ValidationError"): 3,
    ("app/api/routes/document_agent.py", "ValidationError"): 3,
    ("app/api/routes/document_comments.py", "ValidationError"): 1,
    ("app/api/routes/forge_token.py", "GatewayUnavailableError"): 5,
    ("app/api/routes/git_http.py", "ValidationError"): 3,
    ("app/api/routes/github_install.py", "GatewayUnavailableError"): 1,
    ("app/api/routes/integrations.py", "ValidationError"): 2,
    ("app/api/routes/library.py", "ValidationError"): 3,
    ("app/api/routes/llm_proxy.py", "GatewayUnavailableError"): 3,
    ("app/api/routes/llm_proxy.py", "ValidationError"): 4,
    ("app/api/routes/local_dirs.py", "UnauthorizedError"): 1,
    ("app/api/routes/local_dirs.py", "ValidationError"): 1,
    ("app/api/routes/memory.py", "ValidationError"): 1,
    ("app/api/routes/memory_files.py", "ValidationError"): 11,
    ("app/api/routes/notification_preferences.py", "ValidationError"): 1,
    ("app/api/routes/project_context.py", "ValidationError"): 2,
    ("app/api/routes/project_environment.py", "ValidationError"): 6,
    ("app/api/routes/project_sites.py", "AppError"): 1,
    ("app/api/routes/project_skills.py", "ValidationError"): 6,
    ("app/api/routes/projects.py", "ValidationError"): 9,
    ("app/api/routes/projects_artifacts.py", "ValidationError"): 1,
    ("app/api/routes/projects_run_config.py", "ValidationError"): 4,
    ("app/api/routes/remote_mcp.py", "AppError"): 3,
    ("app/api/routes/remote_mcp.py", "ValidationError"): 2,
    ("app/api/routes/room_files.py", "ValidationError"): 4,
    ("app/api/routes/sandbox.py", "UnauthorizedError"): 1,
    ("app/api/routes/topics.py", "ValidationError"): 11,
    ("app/api/routes/topics_attachments.py", "ValidationError"): 10,
    ("app/api/routes/topics_compute.py", "ValidationError"): 9,
    ("app/api/routes/topics_deliveries.py", "ValidationError"): 1,
    ("app/api/routes/topics_documents.py", "ValidationError"): 12,
    ("app/api/routes/topics_file_sources.py", "ValidationError"): 1,
    ("app/api/routes/topics_messages.py", "ValidationError"): 5,
    ("app/api/routes/topics_shown.py", "ValidationError"): 7,
    ("app/api/routes/topics_title.py", "ValidationError"): 1,
    ("app/api/routes/webhooks.py", "ValidationError"): 2,
    ("app/api/routes/workspace.py", "GatewayUnavailableError"): 1,
    ("app/api/routes/workspace.py", "ValidationError"): 4,
    ("app/domain/agent/announce.py", "ValidationError"): 1,
    ("app/domain/agent/chat.py", "ValidationError"): 3,
    ("app/domain/agent/compute_configs.py", "ValidationError"): 4,
    ("app/domain/agent/document/box.py", "ValidationError"): 1,
    ("app/domain/agent/document/question.py", "ValidationError"): 3,
    ("app/domain/agent/document/thread.py", "ValidationError"): 1,
    ("app/domain/agent/gateway_usage.py", "GatewayUnavailableError"): 1,
    ("app/domain/agent/initial_admission.py", "ValidationError"): 1,
    ("app/domain/agent/input_registration.py", "ValidationError"): 1,
    ("app/domain/agent/market.py", "ValidationError"): 1,
    ("app/domain/agent/queries.py", "ValidationError"): 1,
    ("app/domain/agent/runtime.py", "AppError"): 1,
    ("app/domain/agent_credential/services.py", "ValidationError"): 2,
    ("app/domain/agent_instance/services.py", "ValidationError"): 11,
    ("app/domain/block/editing.py", "ValidationError"): 1,
    ("app/domain/block/questions.py", "ValidationError"): 7,
    ("app/domain/delivery/agent.py", "ValidationError"): 4,
    ("app/domain/delivery/note.py", "ValidationError"): 4,
    ("app/domain/delivery/receipts.py", "ValidationError"): 12,
    ("app/domain/delivery/timer.py", "ValidationError"): 4,
    ("app/domain/device/service.py", "ValidationError"): 1,
    ("app/domain/integration/mail.py", "ValidationError"): 2,
    ("app/domain/integration/service.py", "ValidationError"): 18,
    ("app/domain/library/service.py", "ValidationError"): 9,
    ("app/domain/machine/services.py", "ValidationError"): 4,
    ("app/domain/machine/supply.py", "ValidationError"): 2,
    ("app/domain/machine/warm.py", "ValidationError"): 2,
    ("app/domain/membership/services.py", "ValidationError"): 8,
    ("app/domain/memory/files_store.py", "ValidationError"): 1,
    ("app/domain/notification/services.py", "ValidationError"): 3,
    ("app/domain/pin/services.py", "ValidationError"): 2,
    ("app/domain/policy/gate.py", "ValidationError"): 1,
    ("app/domain/preview/office.py", "ValidationError"): 3,
    ("app/domain/project/artifacts.py", "ValidationError"): 11,
    ("app/domain/project/export.py", "GatewayUnavailableError"): 9,
    ("app/domain/project/forge.py", "GatewayUnavailableError"): 22,
    ("app/domain/project/room_files.py", "ValidationError"): 4,
    ("app/domain/project/services.py", "ValidationError"): 1,
    ("app/domain/project_skill/importer.py", "ValidationError"): 15,
    ("app/domain/project_skill/service.py", "ValidationError"): 15,
    ("app/domain/remote_mcp/declared.py", "GatewayUnavailableError"): 1,
    ("app/domain/remote_mcp/http.py", "ValidationError"): 1,
    ("app/domain/remote_mcp/oauth.py", "ValidationError"): 1,
    ("app/domain/remote_mcp/service.py", "ValidationError"): 8,
    ("app/domain/repository/forge_files.py", "GatewayUnavailableError"): 8,
    ("app/domain/repository/forge_files.py", "ValidationError"): 5,
    ("app/domain/repository/service.py", "ValidationError"): 6,
    ("app/domain/review/forge.py", "ValidationError"): 5,
    ("app/domain/review/services/_shared.py", "ValidationError"): 6,
    ("app/domain/review/services/accept.py", "ValidationError"): 13,
    ("app/domain/review/services/cards.py", "ValidationError"): 9,
    ("app/domain/review/services/decisions.py", "ValidationError"): 21,
    ("app/domain/review/services/merge_queue.py", "ValidationError"): 10,
    ("app/domain/review/services/polling.py", "ValidationError"): 3,
    ("app/domain/review/services/reviewers.py", "ValidationError"): 4,
    ("app/domain/room_task/binding.py", "ValidationError"): 2,
    ("app/domain/room_task/presentation.py", "ValidationError"): 1,
    ("app/domain/room_task/snapshots.py", "ValidationError"): 4,
    ("app/domain/routine/schedule.py", "ValidationError"): 8,
    ("app/domain/routine/service.py", "ValidationError"): 17,
    ("app/domain/site/hosting.py", "AppError"): 2,
    ("app/domain/site/hosting.py", "ValidationError"): 3,
    ("app/domain/site/services.py", "ValidationError"): 12,
    ("app/domain/textfile.py", "ValidationError"): 1,
    ("app/domain/thread/services.py", "ValidationError"): 2,
    ("app/domain/topic/services.py", "ValidationError"): 15,
    ("app/domain/topic_membership/services.py", "ValidationError"): 10,
    ("tests/integration/test_accept_pr.py", "ValidationError"): 2,
    ("tests/integration/test_accept_pr_publish.py", "GatewayUnavailableError"): 1,
    ("tests/integration/test_chat_realtime.py", "ValidationError"): 1,
    ("tests/integration/test_gateway_usage.py", "AppError"): 1,
    ("tests/integration/test_github_repository_rename.py", "ValidationError"): 2,
    ("tests/integration/test_native_batch_ownership.py", "ValidationError"): 2,
    ("tests/integration/test_native_completion_commit_cursor.py", "ValidationError"): 1,
    ("tests/integration/test_native_work_termination.py", "ValidationError"): 1,
    ("tests/integration/test_project_agent_models.py", "ValidationError"): 1,
    ("tests/integration/test_project_forge_choice.py", "GatewayUnavailableError"): 1,
    ("tests/integration/test_remote_read_connections.py", "GatewayUnavailableError"): 1,
    ("tests/integration/test_room_file_helpers_move.py", "ValidationError"): 1,
    ("tests/integration/test_target_fence.py", "ValidationError"): 1,
    ("tests/support/ledger_forge.py", "ValidationError"): 2,
    ("tests/unit/test_agent_model_choices.py", "ValidationError"): 1,
    ("tests/unit/test_forge_authorship.py", "GatewayUnavailableError"): 1,
    ("tests/unit/test_forge_committed_files.py", "GatewayUnavailableError"): 1,
    ("tests/unit/test_forge_dependencies.py", "ValidationError"): 1,
    ("tests/unit/test_machine_enrollment.py", "ValidationError"): 1,
    ("tests/unit/test_mail_host_guard.py", "ValidationError"): 3,
    ("tests/unit/test_mail_server.py", "ValidationError"): 1,
    ("tests/unit/test_one_error_tree.py", "AppError"): 1,
    ("tests/unit/test_one_error_tree.py", "GatewayUnavailableError"): 3,
    ("tests/unit/test_one_error_tree.py", "UnauthorizedError"): 1,
    ("tests/unit/test_one_error_tree.py", "ValidationError"): 3,
    ("tests/unit/test_review_forge.py", "ValidationError"): 3,
    ("tests/unit/test_room_send_timing.py", "ValidationError"): 2,
    ("tests/unit/test_routine_schedule.py", "ValidationError"): 2,
    ("tests/unit/test_site_snapshots.py", "ValidationError"): 5,
    ("tests/unit/test_skill_import_github.py", "ValidationError"): 2,
    ("tests/unit/test_task_live_files.py", "GatewayUnavailableError"): 3,
    ("tests/unit/test_task_snapshot_storage.py", "ValidationError"): 2,
    ("tests/unit/test_webhook.py", "ValidationError"): 1,
    ("tests/unit/test_work_model_binding.py", "ValidationError"): 1,
}


def test_uses_of_the_deprecated_errors_only_go_down() -> None:
    # One test, one scan: split in two, xdist would run the scan twice.
    found = _scan()
    grew = {k: v for k, v in found.items() if v > _BASELINE.get(k, 0)}
    assert not grew, (
        "New uses of a deprecated error class; raise the class its docstring "
        f"names instead (app/core/errors.py): {grew}"
    )
    shrank = {
        k: (v, found.get(k, 0)) for k, v in _BASELINE.items() if found.get(k, 0) < v
    }
    assert not shrank, (
        "These uses are gone; lower or delete their lines in _BASELINE "
        f"(was, now): {shrank}"
    )


def test_the_scan_sees_every_spelling() -> None:
    tree = ast.parse(
        "from app.core.errors import ValidationError as VE, NotFoundError\n"
        "from app.core import errors\n"
        "import app.core.errors as E\n"
        "from pydantic import ValidationError as PydanticError\n"
        "raise VE('x')\n"
        "raise errors.AppError('x')\n"
        "raise E.UnauthorizedError()\n"
        "except_ = PydanticError\n"
    )
    assert _uses(tree) == Counter(
        {"ValidationError": 1, "AppError": 1, "UnauthorizedError": 1}
    )
