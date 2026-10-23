"""A repository's single-row ``get_by_id`` comes with a ``get_by_ids`` sibling.

The judgement and its frozen baseline live in
`.claude/scripts/check-get-by-ids-sibling.py`; this test is how the whole-tree
guard group (``TREE_GUARDS``) runs it before a push and on the PR's quick tier.
When it fails, the message names the class: add ``get_by_ids`` on
``app.domain.reads.get_by_ids`` with the same filters as ``get_by_id``.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".claude" / "scripts" / "check-get-by-ids-sibling.py"


def _guard():
    spec = importlib.util.spec_from_file_location("check_get_by_ids_sibling", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_single_read_outside_the_baseline_has_a_batch_sibling():
    guard = _guard()
    result = guard.judge(REPO_ROOT, guard.BASELINE)
    assert result["new"] == [], (
        "这些类有单条 get_by_id 但没有 get_by_ids，在同一个类里补上"
        "（用 app.domain.reads.get_by_ids，过滤条件与 get_by_id 一致）：\n"
        + "\n".join(result["new"])
    )
    assert result["stale"] == [], (
        "BASELINE 里这些类已不再缺 get_by_ids，从 "
        ".claude/scripts/check-get-by-ids-sibling.py 的 BASELINE 删掉：\n"
        + "\n".join(result["stale"])
    )


def test_the_guard_still_bites_on_its_own_cases():
    assert _guard()._self_test() == 0
