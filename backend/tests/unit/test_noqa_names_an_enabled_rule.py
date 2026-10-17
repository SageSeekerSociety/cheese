"""Ratchet: every ``# noqa`` in ``backend/app`` names a rule that runs.

``backend/pyproject.toml`` selects E, F, I, UP, B, DTZ and A003 and ignores
UP042. A directive naming anything else — ``# noqa: BLE001`` over a bare
``except``, say — suppresses nothing and cannot fail: no linter reads it, so
nothing ever tells the reader it is inert. This test counts those inert
directives per (file, rule) and holds them to the numbers written below.

Like the other ratchets here it is red both ways. A count that grew, or a pair
that newly appeared, is a new inert directive: delete it, or enable its rule so
the directive does what it says. A count that shrank is one that was removed:
lower or delete its line. When ``_BASELINE`` is empty every directive names a
rule that runs, and ``RUF100`` in ``select`` can hold the rule without this
file.

Directives are read from the ``COMMENT`` tokens ``tokenize`` yields, so a
``"# noqa: BLE001"`` inside a string literal is not a directive. Every spelling
ruff reads is read: a clause anywhere in a comment (``## noqa: X``,
``# type: ignore  # noqa: X``), whitespace before either colon, the file-level
``# ruff: noqa: X`` where it is alone on its line, codes separated by commas or
whitespace, and an empty item where a code would be (``# noqa: ,BLE001``),
which ruff warns about and then reads. A trailing explanation is not part of
the code list. A bare ``# noqa`` with no rule list suppresses everything and is
never inert; it is not counted.

A selector that names a group (``E``, ``BLE``) enables every rule in that
group; one that names a single rule (``A003``) enables only that one, and
``ALL`` enables everything. ``B`` and ``BLE`` are different groups even though
one spells the other's first letter, which is why this compares whole group
names rather than string prefixes. ``ignore`` and ``extend-ignore`` subtract
from the resolved set, so a directive naming an ignored rule is inert as well.

Two limits: ``per-file-ignores`` is not modelled (none of them covers
``app/`` today), and neither are partial prefixes such as ``E5``. A code-shaped
word that starts the list is read as a code; ruff drops codes it does not know,
so such a line is counted here and not reported by RUF100.

``ruff check --extend-select RUF100 backend/app`` agrees on the tree this
baseline was measured on: its ``non-enabled:`` diagnostics are exactly the
pairs below. It also reports enabled-but-unused directives (``unused:``), which
this test does not count. Run it if this test and the tree disagree.
"""

import re
import tokenize
import tomllib
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = BACKEND_ROOT / "pyproject.toml"
_APP = BACKEND_ROOT / "app"

#: A ``noqa`` clause, wherever it sits in a comment: ruff reads the whole
#: comment, so a second hash inside it (a doubled-hash prefix, or a trailing
#: ``type: ignore`` comment) does not end the search. The ``ruff:`` prefix is
#: the file-level form; ruff honours it only when it starts a comment on a line
#: of its own, and warns about it anywhere else. Either colon may have
#: whitespace before it. ``sep`` is the colon that introduces a rule list; its
#: absence is the bare directive that suppresses everything.
_NOQA = re.compile(
    r"#\s*(?P<file_level>ruff\s*:\s*)?noqa\b\s*(?P<sep>:\s*)?(?P<codes>[^#]*)",
    re.IGNORECASE,
)
#: One rule code: ``BLE001``. The clause is a comma-separated list, so a code
#: sits at the head of it or right after a comma or whitespace — an empty item
#: (`,,BLE001`) leaves a separator where a code would be. Anything else ends
#: the list, which is what keeps a trailing explanation out of the count.
_CODE = re.compile(r"[A-Z][A-Z0-9]*\d")
_SEPARATOR = re.compile(r"[\s,]+")


def _listed_codes(clause: str) -> list[str]:
    """The codes the clause names, in order."""
    codes: list[str] = []
    # A list may open with a separator: ruff warns about the missing code and
    # reads the rest, so a comma before the first code still names it.
    separator = _SEPARATOR.match(clause)
    pos = separator.end() if separator else 0
    while (match := _CODE.match(clause, pos)) is not None:
        codes.append(match.group())
        pos = match.end()
        separator = _SEPARATOR.match(clause, pos)
        if separator is None:
            break
        pos = separator.end()
    return codes


def _ruleset() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The selectors ruff runs with, and the ones it subtracts.

    ``extend-select`` and ``extend-ignore`` are read where the config has them.
    """
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["tool"]["ruff"][
        "lint"
    ]

    def listed(key: str) -> tuple[str, ...]:
        return tuple(config.get(key, ()))

    selectors = listed("select") + listed("extend-select")
    ignores = listed("ignore") + listed("extend-ignore")
    return selectors, ignores


def _covered(code: str, selectors: tuple[str, ...]) -> bool:
    """Whether a selector list names ``code``: the rule, its group, or ALL.

    Matching is per group, not per string prefix, so ``B`` does not cover
    ``BLE001`` even though one spells the other's first letter.
    """
    group = code.rstrip("0123456789")
    return any(
        s == code or s == "ALL" or (s.isalpha() and s == group) for s in selectors
    )


def _enabled(code: str, selectors: tuple[str, ...], ignores: tuple[str, ...]) -> bool:
    """Whether ruff runs ``code``: selected, and not ignored.

    ``ignore`` subtracts whatever a selector enabled — the project's own
    ``UP042`` is inert for exactly that reason.
    """
    return _covered(code, selectors) and not _covered(code, ignores)


def _directives(path: Path) -> Counter[str]:
    """Rule codes named by the ``# noqa`` comments in one file, counted."""
    found: Counter[str] = Counter()
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.COMMENT:
                continue
            # The file-level form only counts where ruff honours it: alone on
            # its line, at the start of the comment.
            alone = not token.line[: token.start[1]].strip()
            for match in _NOQA.finditer(token.string):
                if match.group("file_level") and not (match.start() == 0 and alone):
                    continue
                if match.group("sep") is None:
                    continue  # a bare directive suppresses everything
                found.update(_listed_codes(match.group("codes")))
    return found


def _scan() -> dict[tuple[str, str], int]:
    selectors, ignores = _ruleset()
    found: dict[tuple[str, str], int] = {}
    for path in sorted(_APP.rglob("*.py")):
        # Tokenizing the whole tree costs more than a pure test may; a file
        # that never spells the word cannot hold a directive.
        if b"noqa" not in path.read_bytes().lower():
            continue
        inert = {
            code: count
            for code, count in _directives(path).items()
            if not _enabled(code, selectors, ignores)
        }
        if not inert:
            continue
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for code, count in inert.items():
            found[(rel, code)] = count
    return found


# (file, rule) -> directives naming a rule that is not selected. Only ever
# lowered or deleted.
_BASELINE: dict[tuple[str, str], int] = {
    ("app/api/routes/accept.py", "BLE001"): 1,
    ("app/api/routes/agent_control.py", "BLE001"): 1,
    ("app/api/routes/assistant.py", "BLE001"): 1,
    ("app/api/routes/connector/devices.py", "BLE001"): 1,
    ("app/api/routes/docs_site.py", "BLE001"): 1,
    ("app/api/routes/docs_visit.py", "BLE001"): 1,
    ("app/api/routes/document_agent.py", "BLE001"): 1,
    ("app/api/routes/execution.py", "BLE001"): 1,
    ("app/api/routes/integrations.py", "BLE001"): 1,
    ("app/api/routes/projects.py", "BLE001"): 1,
    ("app/core/alerting.py", "BLE001"): 2,
    ("app/core/background.py", "BLE001"): 2,
    ("app/core/net_io.py", "PERF203"): 1,
    ("app/core/obs.py", "ANN001"): 3,
    ("app/core/obs.py", "ANN201"): 1,
    ("app/core/obs.py", "BLE001"): 4,
    ("app/core/ownership.py", "BLE001"): 4,
    ("app/core/request_limits.py", "BLE001"): 1,
    ("app/core/single_use_state.py", "BLE001"): 2,
    ("app/core/storage.py", "S324"): 1,
    ("app/domain/agent/admission.py", "BLE001"): 5,
    ("app/domain/agent/central_provider.py", "BLE001"): 1,
    ("app/domain/agent/chat_lifecycle.py", "BLE001"): 1,
    ("app/domain/agent/chat_live_input.py", "BLE001"): 3,
    ("app/domain/agent/chat_places.py", "BLE001"): 1,
    ("app/domain/agent/credits_notice.py", "BLE001"): 3,
    ("app/domain/agent/device_hub/inbound.py", "BLE001"): 1,
    ("app/domain/agent/device_hub_rpc.py", "BLE001"): 2,
    ("app/domain/agent/device_provider.py", "BLE001"): 1,
    ("app/domain/agent/device_storage.py", "BLE001"): 2,
    ("app/domain/agent/gateway.py", "BLE001"): 6,
    ("app/domain/agent/gateway_admin.py", "BLE001"): 1,
    ("app/domain/agent/gateway_catalog.py", "BLE001"): 1,
    ("app/domain/agent/gateway_spend.py", "BLE001"): 1,
    ("app/domain/agent/gateway_usage.py", "BLE001"): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/client.py",
        "BLE001",
    ): 2,
    (
        "app/domain/agent/harness/claude_code/remote_execution/forwarded_fs.py",
        "BLE001",
    ): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/private_egress.py",
        "BLE001",
    ): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/release.py",
        "S102",
    ): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/runtime_commands.py",
        "BLE001",
    ): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/shell_prefix.py",
        "BLE001",
    ): 1,
    (
        "app/domain/agent/harness/claude_code/remote_execution/shell_stop.py",
        "BLE001",
    ): 1,
    ("app/domain/agent/harness/claude_code/runner.py", "BLE001"): 1,
    ("app/domain/agent/harness/codex/runner.py", "BLE001"): 2,
    ("app/domain/agent/harness/codex/tools.py", "BLE001"): 1,
    ("app/domain/agent/harness/codex/tools.py", "S102"): 1,
    ("app/domain/agent/harness/driven/runner.py", "BLE001"): 2,
    ("app/domain/agent/harness/pi/catalog.py", "S102"): 1,
    ("app/domain/agent/harness/pi/jobs.py", "BLE001"): 1,
    ("app/domain/agent/harness/pi/mcp.py", "BLE001"): 1,
    ("app/domain/agent/harness/pi/relay.py", "BLE001"): 1,
    ("app/domain/agent/harness/pi/runner.py", "BLE001"): 6,
    ("app/domain/agent/host_failure.py", "BLE001"): 2,
    ("app/domain/agent/machine_tunnel.py", "BLE001"): 1,
    ("app/domain/agent/machine_tunnel.py", "S323"): 1,
    ("app/domain/agent/mcp_notice.py", "BLE001"): 1,
    ("app/domain/agent/preview_tunnel.py", "BLE001"): 1,
    ("app/domain/agent/preview_tunnel.py", "S323"): 1,
    ("app/domain/agent/prewarm.py", "BLE001"): 2,
    ("app/domain/agent/recovery.py", "BLE001"): 2,
    ("app/domain/agent/resource_cleanup.py", "S324"): 1,
    ("app/domain/agent/room_events.py", "BLE001"): 3,
    ("app/domain/agent/run_records.py", "BLE001"): 3,
    ("app/domain/agent/runtime.py", "BLE001"): 3,
    ("app/domain/agent/runtime_orphans.py", "BLE001"): 3,
    ("app/domain/agent/session_host/claude_code.py", "BLE001"): 3,
    ("app/domain/agent/session_host/consumptions.py", "BLE001"): 5,
    ("app/domain/agent/turn/intake/completion.py", "BLE001"): 1,
    ("app/domain/agent/turn/intake/hooks.py", "BLE001"): 1,
    ("app/domain/agent/turn/intake/messages.py", "BLE001"): 3,
    ("app/domain/agent/turn/intake/native_turns.py", "BLE001"): 1,
    ("app/domain/agent/turn/intake/notes.py", "BLE001"): 1,
    ("app/domain/agent/turn/state/live.py", "BLE001"): 1,
    ("app/domain/agent/turn/steps/execute.py", "BLE001"): 4,
    ("app/domain/agent/turn/steps/reminders.py", "BLE001"): 2,
    ("app/domain/agent/turn/steps/send.py", "BLE001"): 1,
    ("app/domain/agent/turn/store/events.py", "BLE001"): 2,
    ("app/domain/agent/turn/store/intervals.py", "BLE001"): 1,
    ("app/domain/agent/turn_adoption.py", "BLE001"): 1,
    ("app/domain/agent/turn_inputs.py", "BLE001"): 2,
    ("app/domain/agent/turn_ledger.py", "BLE001"): 1,
    ("app/domain/backend_log.py", "BLE001"): 1,
    ("app/domain/docs_site/access.py", "BLE001"): 1,
    ("app/domain/docs_site/limits.py", "BLE001"): 2,
    ("app/domain/docs_site/retrieval.py", "BLE001"): 1,
    ("app/domain/docs_site/tools.py", "BLE001"): 1,
    ("app/domain/docs_site/visits.py", "BLE001"): 1,
    ("app/domain/documents/compare.py", "BLE001"): 1,
    ("app/domain/documents/convert.py", "BLE001"): 2,
    ("app/domain/documents/revisions.py", "BLE001"): 3,
    ("app/domain/documents/spreadsheet.py", "BLE001"): 2,
    ("app/domain/environment_diagnosis.py", "BLE001"): 1,
    ("app/domain/feature_stats/features/task_naming.py", "BLE001"): 1,
    ("app/domain/fetch/guard.py", "BLE001"): 1,
    ("app/domain/fetch/layers.py", "BLE001"): 4,
    ("app/domain/fetch/service.py", "BLE001"): 1,
    ("app/domain/integration/service.py", "BLE001"): 1,
    ("app/domain/library/records.py", "BLE001"): 1,
    ("app/domain/machine/lease_claim.py", "BLE001"): 1,
    ("app/domain/machine/session_work.py", "BLE001"): 2,
    ("app/domain/machine/warm.py", "BLE001"): 1,
    ("app/domain/memory/redundant.py", "BLE001"): 1,
    ("app/domain/platform_stats/pipeline.py", "PLC2701"): 1,
    ("app/domain/preview/office.py", "BLE001"): 3,
    ("app/domain/project/protection.py", "BLE001"): 1,
    ("app/domain/project_situation/service.py", "BLE001"): 1,
    ("app/domain/project_situation/writer.py", "BLE001"): 1,
    ("app/domain/remote_mcp/declared.py", "BLE001"): 2,
    ("app/domain/remote_mcp/service.py", "BLE001"): 1,
    ("app/domain/remote_mcp/upstream.py", "BLE001"): 1,
    ("app/domain/repository/identity.py", "BLE001"): 5,
    ("app/domain/review/comment_publish.py", "BLE001"): 1,
    ("app/domain/review/comments.py", "BLE001"): 3,
    ("app/domain/review/document_anchor.py", "BLE001"): 1,
    ("app/domain/review/events.py", "ANN001"): 1,
    ("app/domain/review/events.py", "BLE001"): 2,
    ("app/domain/review/forge.py", "BLE001"): 2,
    ("app/domain/review/gate_sweep.py", "BLE001"): 1,
    ("app/domain/review/landing_watch.py", "BLE001"): 1,
    ("app/domain/review/pr_poll.py", "BLE001"): 3,
    ("app/domain/review/pr_publish.py", "ANN001"): 1,
    ("app/domain/review/pr_publish.py", "BLE001"): 2,
    ("app/domain/review/pr_publish_payload.py", "BLE001"): 1,
    ("app/domain/review/room_notices.py", "BLE001"): 2,
    ("app/domain/review/services/accept.py", "BLE001"): 3,
    ("app/domain/review/services/cards.py", "BLE001"): 1,
    ("app/domain/review/services/decisions.py", "BLE001"): 1,
    ("app/domain/review/services/merge_queue.py", "BLE001"): 4,
    ("app/domain/review/services/polling/advance.py", "BLE001"): 1,
    ("app/domain/review/services/polling/card_poll.py", "BLE001"): 2,
    ("app/domain/review/services/polling/clients.py", "ANN202"): 1,
    ("app/domain/review/services/polling/clients.py", "BLE001"): 1,
    ("app/domain/review/services/polling/seen_head.py", "BLE001"): 2,
    ("app/domain/review/services/polling/snapshot.py", "BLE001"): 1,
    ("app/domain/review/services/service.py", "ANN202"): 1,
    ("app/domain/review/task_landing.py", "BLE001"): 2,
    ("app/domain/room_cleanup/retire.py", "BLE001"): 1,
    ("app/domain/room_task/checkouts.py", "BLE001"): 1,
    ("app/domain/room_task/naming.py", "BLE001"): 2,
    ("app/domain/room_task/naming_model.py", "BLE001"): 2,
    ("app/domain/service_keys.py", "BLE001"): 1,
    ("app/domain/usage/pricing.py", "BLE001"): 1,
    ("app/domain/wake_watch/observe.py", "BLE001"): 1,
    ("app/domain/webhook/service.py", "BLE001"): 1,
    ("app/main.py", "BLE001"): 8,
}


def test_inert_noqa_directives_only_go_down() -> None:
    # One test, one scan: split in two, xdist would run the scan twice.
    found = _scan()
    grew = {
        key: (found[key], _BASELINE.get(key, 0))
        for key in found
        if found[key] > _BASELINE.get(key, 0)
    }
    assert not grew, (
        "New `# noqa` directives name a rule this project does not select, so "
        "they suppress nothing. Delete the directive, or put the rule in "
        f"pyproject.toml's select so the directive means something: {grew}"
    )
    shrank = {
        key: (was, found.get(key, 0))
        for key, was in _BASELINE.items()
        if found.get(key, 0) < was
    }
    assert not shrank, (
        "These inert directives are gone; lower or delete their lines in "
        f"_BASELINE (was, now): {shrank}"
    )


def test_the_scan_reads_comments_not_strings(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:  # noqa: BLE001\n"
        "        pass\n"
        'MARKER = "# noqa: BLE001"\n'
        "def g():\n"
        "    return 1  # noqa: F401\n"
        "X = 1  # noqa\n"
        "Y = 2  # noqa:  # nothing here\n",
        encoding="utf-8",
    )
    assert _directives(sample) == Counter({"BLE001": 1, "F401": 1})


def test_every_spelling_ruff_accepts_is_read(tmp_path: Path) -> None:
    # Ruff reads a noqa clause anywhere in the comment and takes the codes that
    # follow, so a clause this scan misses is an inert directive it cannot see
    # — the ratchet would stay green while the tree grows them.
    sample = tmp_path / "sample.py"
    sample.write_text(
        "# ruff : noqa: , F401\n"
        "import os  # noqa : F401\n"
        "import sys  ## noqa: F401\n"
        "import re  # type: ignore  # noqa: F401\n"
        "import io  # noqa: F401 BLE001\n"
        "import abc  # noqa: F401,BLE001\n"
        "x = 1  # noqa: F401 -- os is re-exported\n"
        "y = 2  # ruff: noqa: F401\n"
        "z = 3  # noqa: BLE001 — surface failures (spec H4)\n"
        "q = 4  # noqa: ,BLE001\n"
        "w = 5  # ruff : noqa: , F401\n"
        "v = 6  # noqa: BLE001, BLE\n",
        encoding="utf-8",
    )
    # H4 is prose and BLE is not a code: the list ends at the first thing that
    # is not one. The two `ruff:` clauses after code are not file-level: ruff
    # warns that they must be alone on their line and reads neither.
    assert _directives(sample) == Counter({"F401": 7, "BLE001": 5})


def test_a_selector_covers_its_own_group_not_a_spelled_prefix() -> None:
    # Bugbear is B; blind-except is BLE. Reading "B" as a string prefix would
    # call every BLE001 in the tree enabled and the ratchet would never fire.
    assert _enabled("E501", ("E",), ())
    assert _enabled("BLE001", ("BLE",), ())
    assert not _enabled("BLE001", ("B",), ())
    assert _enabled("A003", ("A003",), ())
    assert not _enabled("A004", ("A003",), ())
    # ALL selects everything, and ignore subtracts what a selector enabled.
    assert _enabled("BLE001", ("ALL",), ())
    assert not _enabled("UP042", ("UP",), ("UP042",))
    assert _enabled("UP035", ("UP",), ("UP042",))
    assert not _enabled("BLE001", ("E",), ())
