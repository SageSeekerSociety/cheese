"""Holds ``.claude/scripts/test-changed.py`` to the selection it claims to make.

The entry point's whole promise is that it runs *the tests this change reaches*,
so what is worth testing is the case where it reaches nothing: a change outside
the import graph (a script under ``.claude/scripts/``, whose tests load it by
path) selects only the always-on tree guards, and a run of those exits 0 whatever
the change did. The tests here pin the two answers — the change is judged, or the
run says it was not — because the second one is what a green exit code would
otherwise claim on the first's behalf.

Nothing here runs pytest or Vitest: the plan is an object, and it is the object
that decides. A test that ran the real suites would be measuring this machine.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".claude" / "scripts" / "test-changed.py"

BACKEND_ONLY = {"backend": True, "frontend": False}
FRONTEND_ONLY = {"backend": False, "frontend": True}


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("test_changed_entry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def platform(runner):
    """``ci-fast.py`` itself: the shared path map and the planner live behind it."""
    return runner.load_platform(ROOT)


def _tree(tmp_path: Path) -> Path:
    """A repo-shaped tree: one backend module with one unit test that imports it.

    Only what the planner reads — the AST, the paths, the narrow TS project's
    ``extends`` target and its ``node_modules`` symlink — has to be here.
    """
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "touched.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (tmp_path / "backend" / "tests" / "unit" / "test_touched.py").write_text(
        "from app.touched import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    (tmp_path / "frontend" / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    return tmp_path


def test_a_backend_change_selects_its_consumer(runner, platform, tmp_path):
    root = _tree(tmp_path)
    plan, omissions = runner.plan_for(
        platform,
        root,
        ["backend/app/touched.py"],
        BACKEND_ONLY,
        [],
        tmp_path,
        3072,
        False,
    )
    assert [entry["id"] for entry in plan] == ["backend-pure"]
    assert not omissions
    entry = plan[0]
    assert entry["evidence"]["required"] == ["tests/unit/test_touched.py"]
    assert entry["evidence"]["selection"].startswith("reverse transitive")
    assert "tests/unit/test_touched.py" in entry["command"]
    assert entry["cwd"] == root / "backend"
    # A type error is the push's verdict, not the edit's; the plan carries one
    # and this entry point drops it.
    assert all("typecheck" not in entry["id"] for entry in plan)


def test_a_frontend_change_seeds_related_and_drops_typecheck(
    runner, platform, tmp_path
):
    root = _tree(tmp_path)
    widget = root / "frontend" / "src" / "components" / "Widget.vue"
    widget.parent.mkdir(parents=True)
    widget.write_text("<template><div/></template>\n", encoding="utf-8")
    changed = ["frontend/src/components/Widget.vue"]
    plan, _ = runner.plan_for(
        platform, root, changed, FRONTEND_ONLY, changed, tmp_path, 3072, False
    )
    assert [entry["id"] for entry in plan] == ["frontend-unit", "frontend-i18n"]
    unit = plan[0]
    assert unit["evidence"]["mode"] == "related"
    assert unit["evidence"]["roots"] == ["src/components/Widget.vue"]
    assert "related" in unit["command"]
    # The empty-graph guard, without which a run that selected no test reports
    # the exit code of the specs that always run.
    assert "--no-passWithNoTests" in unit["command"]
    assert unit["cwd"] == root / "frontend"


def test_a_change_the_graph_cannot_reach_is_dropped_not_run(runner, platform, tmp_path):
    """A script under ``.claude/scripts/`` selects only the tree guards."""
    root = _tree(tmp_path)
    (root / ".claude" / "scripts").mkdir(parents=True)
    (root / ".claude" / "scripts" / "some-tool.py").write_text("", encoding="utf-8")
    plan, omissions = runner.plan_for(
        platform,
        root,
        [".claude/scripts/some-tool.py"],
        BACKEND_ONLY,
        [],
        tmp_path,
        3072,
        False,
    )
    assert plan == []
    assert any(o["id"] == "backend-pure" for o in omissions)


def test_a_frontend_catalog_still_runs_backend_tree_guards(runner, platform, tmp_path):
    """The one case where guards-only *is* the judgement: the catalog is read back."""
    root = _tree(tmp_path)
    plan, _ = runner.plan_for(
        platform,
        root,
        ["frontend/src/i18n/messages/x/y.json"],
        BACKEND_ONLY,
        [],
        tmp_path,
        3072,
        True,
    )
    assert [entry["id"] for entry in plan] == ["backend-pure"]
    assert plan[0]["evidence"]["required"] == []


def test_scope_follows_the_shared_path_map(runner, platform):
    select, paths_map = platform.load_select()

    def scope(changed, base_sha="cafe1234"):
        return runner.scope_for(
            changed, select, paths_map, base_sha, platform.DOC_GLOBS
        )

    backend, _, unknown, _ = scope(["backend/app/domain/user/mail_quota.py"])
    assert backend["backend"] and not backend["frontend"] and not unknown

    frontend, _, _, _ = scope(["frontend/src/components/Widget.vue"])
    assert frontend["frontend"] and not frontend["backend"]

    widened, _, unknown, _ = scope(["weird/thing.bin"])
    assert unknown == ["weird/thing.bin"], "路径映射没点名的路径按未知道路处理"
    assert widened["backend"] and widened["frontend"]

    prose, _, unknown, _ = scope(["docs/topics/anything.md"])
    assert not prose["backend"] and not prose["frontend"] and not unknown

    script, _, _, _ = scope([".claude/scripts/trace-sweep.py"])
    assert script["backend"], "脚本的后端单测按路径读它，导入图看不见，这里必须补上"

    catalog, _, _, guard = scope(["frontend/src/i18n/messages/zh-CN/other/x.json"])
    assert guard and catalog["backend"]

    unattributed, _, _, _ = scope(["backend/app/x.py"], base_sha=None)
    assert all(unattributed.values()), "没有 merge base 时两个套件都跑"


def test_strict_refuses_the_whole_suite_and_allows_a_pinpointed_one(runner):
    assert runner.pinpointed(
        {
            "id": "backend-pure",
            "evidence": {
                "required": ["tests/unit/x.py"],
                "selection": "reverse transitive Python imports",
            },
        }
    )
    assert not runner.pinpointed(
        {
            "id": "backend-pure",
            "evidence": {
                "required": ["tests/unit/x.py"],
                "selection": "full pure fallback: no static consumers for app.x",
            },
        }
    )
    assert runner.pinpointed({"id": "frontend-unit", "evidence": {"mode": "related"}})
    assert not runner.pinpointed({"id": "frontend-unit", "evidence": {"mode": "full"}})
    assert runner.pinpointed({"id": "frontend-i18n", "evidence": {}})
