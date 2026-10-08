"""An empty ``downgrade()`` on main says why it is empty.

A migration whose ``downgrade()`` is a bare ``pass`` reads the same whether the
author decided there was nothing to restore or never wrote one, and that is the
whole of what a reader has: the schema does not come back by hand (there is no
``db:downgrade`` task), so what the function says is the only thing to act on.

``check-migration-safety.py`` enforces this for the migrations a branch ADDS
(the ``empty-downgrade`` rule). This test holds the ones already on main to the
same bar, so that a later edit cannot quietly put one back.

The Alembic template stubs already on main — a ``Downgrade schema.`` docstring
and nothing else — are the exception listed below. They predate the rule and are
left as they are.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
VERSIONS = REPO / "backend" / "alembic" / "versions"
GATE = REPO / ".claude" / "scripts" / "check-migration-safety.py"

#: Migrations on main whose downgrade is the untouched Alembic template
#: (``Downgrade schema.`` + ``pass``). Frozen history; not rewritten.
TEMPLATE_STUBS = frozenset(
    {
        "06b31e24626f_join_task_search_bm25_back_onto_the_.py",
        "4006c4e8b583_fusion_unify_product_cheesex_migration_.py",
        "504ece6e60ea_merge_accept_card_pr_fields_upstream_.py",
        "a9c7d3e4b210_merge_admin_subscriptions_user_consent.py",
        "b2958d64e679_merge_webhook_tokens_git_installations_.py",
        "b5045bf862fe_converge_twin_duplicate_merges_.py",
        "c2f677c441f0_converge_duplicate_2026_08_09_accept_.py",
        "c9f4a2e7d153_merge_fb56_provenance_and_upstream_heads.py",
        "e15e207f8bef_merge_parallel_feature_heads_gate_roles_.py",
        "fbd4bf2a51b6_merge_duplicate_merge_migrations_.py",
    }
)


def _gate_empty_downgrade():
    """The gate's own judge, so this test and the gate share one rule."""
    spec = importlib.util.spec_from_file_location("check_migration_safety", GATE)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules[__module__],
    # so the module has to be registered under the name it was loaded with.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.empty_downgrade


def test_only_the_template_stubs_have_an_undocumented_empty_downgrade():
    empty_downgrade = _gate_empty_downgrade()
    undocumented = {
        path.name
        for path in sorted(VERSIONS.glob("*.py"))
        if empty_downgrade(path.read_text(encoding="utf-8"))
    }
    assert undocumented == set(TEMPLATE_STUBS), sorted(undocumented)
