"""Holds ``.claude/scripts/test-changed.py`` to the selection it claims to make.

The entry point's whole promise is that it runs *the tests this change reaches*,
so what is worth testing is the case where it reaches nothing: a change outside
the import graph selects no unit test, and the run has to say so rather than
report the exit code of the specs that always run. The tests here pin the two
answers — the change is judged, or the run says it was not — and the one input
that decides between them: the flag ci-fast computes to say "this cannot be
narrowed", which the entry point must forward rather than leave at its default.

Nothing here runs pytest or Vitest: the plan is an object, and it is the object
that decides. A test that ran the real suites would be measuring this machine.
"""

import importlib.util
import json
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
        conservative=False,
    )
    assert [entry["id"] for entry in plan] == ["backend-pure"]
    assert not omissions
    entry = plan[0]
    assert entry["evidence"]["required"] == ["tests/unit/test_touched.py"]
    # Pinpointed rather than merely non-empty: ``--strict`` keeps this entry only
    # because ``required`` names the tests the change reaches.
    assert runner.pinpointed(platform, entry)
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
        platform,
        root,
        changed,
        FRONTEND_ONLY,
        changed,
        tmp_path,
        3072,
        False,
        conservative=False,
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
    """A backend-named path that is no backend module selects only the tree guards.

    ``deploy/metering-proxy/`` routes to the backend suite — it can change what
    the tests do — but nothing under ``app/`` changed, so the AST graph pinpoints
    no unit test and the always-on tree guards are all that is left. Running them
    and reporting green would be a verdict about a change none of them judges.
    """
    root = _tree(tmp_path)
    plan, omissions = runner.plan_for(
        platform,
        root,
        ["deploy/metering-proxy/cheese_billing_core.py"],
        BACKEND_ONLY,
        [],
        tmp_path,
        3072,
        False,
        conservative=False,
    )
    assert plan == []
    assert any(o["id"] == "backend-pure" for o in omissions)


def test_a_full_fallback_is_run_rather_than_dropped(runner, platform, tmp_path):
    """An empty ``required`` with a fallback behind it is the whole pure suite.

    That is what ci-fast runs for a backend module with no static consumers, and
    it shares its ``required == []`` with the guards-only selection above. Reading
    the empty list as "nothing to judge" would turn a real run into exit 2 and
    leave ``--strict`` nothing to refuse, so the two are told apart by the
    selection, not by the list.
    """
    root = _tree(tmp_path)
    lonely = root / "backend" / "app" / "domain" / "lonely.py"
    lonely.parent.mkdir(parents=True)
    lonely.write_text("X = 1\n", encoding="utf-8")
    plan, omissions = runner.plan_for(
        platform,
        root,
        ["backend/app/domain/lonely.py"],
        BACKEND_ONLY,
        [],
        tmp_path,
        3072,
        False,
        conservative=False,
    )
    assert [entry["id"] for entry in plan] == ["backend-pure"]
    assert plan[0]["evidence"]["required"] == []
    assert not omissions
    # What separates this from the dropped guards-only case is ``files``, not the
    # prose reason next to it: a fallback runs the whole pure suite.
    assert not runner.only_guards(platform, plan[0])
    # Kept so that --strict has a refusal to make; without --strict it runs.
    assert not runner.pinpointed(platform, plan[0])


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
        conservative=False,
    )
    assert [entry["id"] for entry in plan] == ["backend-pure"]
    assert plan[0]["evidence"]["required"] == []
    # Cheap, and here the judgement: --strict must keep it, or a catalog change
    # gets no run at all. A full fallback has the same empty ``required`` and is
    # refused — see test_strict_refuses_the_whole_suite_and_allows_a_pinpointed_one.
    assert runner.only_guards(platform, plan[0])
    assert runner.pinpointed(platform, plan[0])


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

    # A script the map names only under ``guards`` is a change with no reachable
    # unit test — not an unknown path, and not a widened backend suite that would
    # judge nothing. The map does name some ``.claude/scripts/`` files for the
    # frontend (``frontend_grade.py`` and the ratchets), so the generalisation
    # stops here; this file is one it names nowhere but ``guards``.
    script, _, unknown, _ = scope([".claude/scripts/trace-sweep.py"])
    assert not script["backend"] and not script["frontend"] and not unknown

    graded, _, _, _ = scope([".claude/scripts/frontend_grade.py"])
    assert graded["frontend"] and not graded["backend"]

    catalog, _, _, guard = scope(["frontend/src/i18n/messages/zh-CN/other/x.json"])
    assert guard and catalog["backend"]

    unattributed, _, _, _ = scope(["backend/app/x.py"], base_sha=None)
    assert all(unattributed.values()), "没有 merge base 时两个套件都跑"


def test_an_unmapped_path_is_judged_by_ci_fasts_selection(runner, platform, tmp_path):
    """The flag that says "this cannot be narrowed" must survive the forward.

    ``ci-fast`` stops narrowing for a path the shared map does not name, and the
    frontend half of that is the whole suite instead of a ``related`` seed. A
    caller that drops the flag keeps the widening of ``scope`` and loses the
    widening of the plan, and what is left for ``weird/thing.bin`` is the two
    i18n guards — a green exit code about a change no unit test judged. Both
    shapes are pinned here so that dropping the flag again fails loudly.
    """
    root = _tree(tmp_path)
    both = {"backend": True, "frontend": True}
    changed = ["weird/thing.bin"]
    widened, _ = runner.plan_for(
        platform,
        root,
        changed,
        both,
        [],
        tmp_path,
        3072,
        False,
        conservative=True,
    )
    assert [entry["id"] for entry in widened] == ["frontend-unit", "backend-pure"]
    assert widened[0]["evidence"]["mode"] == "full"
    assert not runner.only_guards(platform, widened[1])
    assert all(not runner.pinpointed(platform, entry) for entry in widened)
    narrowed, _ = runner.plan_for(
        platform,
        root,
        changed,
        both,
        [],
        tmp_path,
        3072,
        False,
        conservative=False,
    )
    assert [entry["id"] for entry in narrowed] == ["frontend-i18n"]


def test_the_entry_point_widens_an_unmapped_change_before_it_runs_anything(
    runner, platform, tmp_path, monkeypatch, capsys
):
    """``main`` derives the flag from the selection, the way ci-fast derives it.

    Driven through ``main`` rather than through ``plan_for``, because the bug
    this pins was a correct ``plan_for`` called with a flag nobody computed:
    only the real call path shows which plan the entry point would have run.
    """
    root = _tree(tmp_path)
    monkeypatch.setattr(runner, "repo_root", lambda: root)
    monkeypatch.setattr(runner, "load_platform", lambda _root: platform)
    monkeypatch.setattr(
        platform,
        "collect_changes",
        lambda base: (["weird/thing.bin"], "cafe1234", "head", [], [], []),
    )
    assert runner.main(["--plan", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["conservative"] is True, "未知道路必须让选测停止收窄"
    assert report["unknown_paths"] == ["weird/thing.bin"]
    # The widened entries, not the i18n guards alone: ``describe`` writes
    # ``<id>: cd <cwd> && <argv>``, and only the full frontend run has no
    # ``related`` seed.
    assert [line.split(":")[0] for line in report["plan"]] == [
        "frontend-unit",
        "backend-pure",
    ]
    assert "related" not in report["plan"][0]


def test_strict_refuses_the_whole_suite_and_allows_a_pinpointed_one(runner, platform):
    """What decides is the selection's shape, and the reason string next to it is prose.

    The two backend cases below carry their reason strings swapped on purpose: one
    calls itself a fallback while naming the files it reaches, the other reads
    like a pinpoint while naming none. Only the second is a refusal — its
    ``files`` hold a test that is not one of the always-on guards. The first is
    kept whatever it calls itself.
    """
    guards = sorted(platform.GUARD_TESTS)
    assert runner.pinpointed(
        platform,
        {
            "id": "backend-pure",
            "evidence": {
                "required": ["tests/unit/x.py"],
                "files": ["tests/unit/x.py"] + guards,
                "selection": "full pure fallback: no static consumers for app.x",
            },
        },
    )
    assert not runner.pinpointed(
        platform,
        {
            "id": "backend-pure",
            "evidence": {
                "required": [],
                "files": guards + ["tests/unit/test_everything_else.py"],
                "selection": "reverse transitive Python imports plus tree/i18n guards",
            },
        },
    )
    # Guards and nothing else is cheap and is the judgement where it survives
    # ``plan_for`` at all, so --strict keeps it rather than refusing the run.
    assert runner.pinpointed(
        platform,
        {
            "id": "backend-pure",
            "evidence": {
                "required": [],
                "files": guards,
                "selection": "reverse transitive Python imports plus tree/i18n guards",
            },
        },
    )
    assert runner.pinpointed(
        platform, {"id": "frontend-unit", "evidence": {"mode": "related"}}
    )
    assert not runner.pinpointed(
        platform, {"id": "frontend-unit", "evidence": {"mode": "full"}}
    )
    assert runner.pinpointed(platform, {"id": "frontend-i18n", "evidence": {}})


def test_the_fold_keeps_ci_fasts_precedence(runner):
    """A red test outranks a suite that could not start; both outrank unknown.

    Folding by ``max`` over the exit codes gets this backwards for exactly the
    two combinations below, which is why the fold is written out rather than
    computed.
    """
    assert runner.worst(["fail", "unknown"]) == runner.FAIL
    assert runner.worst(["unknown", "pass"]) == runner.UNKNOWN
    assert runner.worst(["blocked", "unknown"]) == runner.BLOCKED
    assert runner.worst(["blocked", "fail"]) == runner.FAIL
    assert runner.worst(["pass", "not_applicable"]) == runner.PASS
    # Nothing ran at all is not a pass: an empty fold must read as unknown.
    assert runner.worst([]) == runner.UNKNOWN
    assert runner.worst(["pass"]) == runner.PASS


def test_the_entry_point_refuses_a_tool_it_cannot_start(
    runner, platform, tmp_path, monkeypatch, capsys
):
    """A missing tool is ``blocked`` (3), not the unknown that severity suggests."""
    root = _tree(tmp_path)
    monkeypatch.setattr(runner, "repo_root", lambda: root)
    monkeypatch.setattr(runner, "load_platform", lambda _root: platform)
    changed = ["backend/app/touched.py"]
    monkeypatch.setattr(
        platform,
        "collect_changes",
        lambda base: (changed, "cafe1234", "head", [], [], []),
    )

    monkeypatch.setattr(
        platform, "execute", lambda *a, **kw: {"output": "uv: not found"}
    )
    monkeypatch.setattr(
        platform,
        "resolve",
        lambda process, evidence: {
            "result": "blocked",
            "seconds": 0.1,
            "reason": "缺少 uv",
        },
    )
    assert runner.main([]) == runner.BLOCKED
    capsys.readouterr()


def test_the_entry_point_returns_unknown_when_nothing_is_selected(
    runner, platform, tmp_path, monkeypatch, capsys
):
    """The empty plan is the case the whole entry point exists to report honestly.

    Pinned as an empty ``plan`` and not only as an exit code: a run that spawns the
    always-on guards and then cannot read their report also exits unknown, and that
    is the shape this entry point exists to avoid.
    """
    root = _tree(tmp_path)
    monkeypatch.setattr(runner, "repo_root", lambda: root)
    monkeypatch.setattr(runner, "load_platform", lambda _root: platform)
    changed = ["deploy/metering-proxy/cheese_billing_core.py"]
    monkeypatch.setattr(
        platform,
        "collect_changes",
        lambda base: (changed, "cafe1234", "head", [], [], []),
    )
    monkeypatch.setattr(
        platform,
        "execute",
        lambda *a, **kw: pytest.fail("选测为空时不该起任何进程"),
    )
    assert runner.main(["--plan", "--json"]) == runner.UNKNOWN
    assert json.loads(capsys.readouterr().out)["plan"] == []


def _report(capsys):
    """The JSON part of the run's stdout.

    ``--json`` guards the plan printout but not the ``--strict`` refusal lines, so a
    refusing run writes prose before the object.
    """
    out = capsys.readouterr().out
    return json.loads(out[out.index("{") :])


def test_strict_keeps_a_guards_only_run_it_would_otherwise_refuse(
    runner, platform, tmp_path, monkeypatch, capsys
):
    """A plan whose every entry is cheap is run, even when one of them is the guards.

    The plan is handed to ``main`` directly: the synthetic tree cannot produce a
    ``related`` frontend seed (its one TypeScript project is the empty config
    ``_tree`` writes, so the frontend always resolves to the full suite there), and
    the pair below is exactly what the real tree plans for a catalog plus a source
    root. Without the guards-only exemption ``--strict`` sees a loose entry and
    exits 2 having run nothing.
    """

    def entry(id_, evidence):
        return {"id": id_, "cwd": tmp_path, "command": ["true"], "evidence": evidence}

    guards_only = entry(
        "backend-pure", {"required": [], "files": sorted(platform.GUARD_TESTS)}
    )
    related = entry("frontend-unit", {"mode": "related"})
    full = entry("frontend-unit", {"mode": "full"})

    root = _tree(tmp_path)
    monkeypatch.setattr(runner, "repo_root", lambda: root)
    monkeypatch.setattr(runner, "load_platform", lambda _root: platform)
    monkeypatch.setattr(
        runner, "plan_for", lambda *a, **kw: ([guards_only, related], [])
    )

    assert runner.main(["--strict", "--plan", "--json"]) == runner.PASS
    assert "strict_refused" not in _report(capsys)

    monkeypatch.setattr(runner, "plan_for", lambda *a, **kw: ([guards_only, full], []))
    assert runner.main(["--strict", "--plan", "--json"]) == runner.UNKNOWN
    assert _report(capsys)["strict_refused"] == ["frontend-unit"]
