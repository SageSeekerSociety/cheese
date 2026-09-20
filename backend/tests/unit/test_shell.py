"""壳 (shell) — same inheritance as the 机构协议, one catalog, no forks.

What these pin is the chain and its precedence, because getting the order wrong
is silent: a project that should run 课程 quietly runs 现状 instead, and the only
symptom is a teacher saying 「怎么还是老样子」. The three levels are
Project.settings → 赛题 override → 项目集 → `default`, and undeclared must land on
`default`, which is today's interface screen for screen.
"""

from pathlib import Path
from types import SimpleNamespace

from app.domain.shell.catalog import CATALOG, DEFAULT_SHELL_NAME, lookup
from app.domain.shell.service import resolve_shell
from app.domain.task.protocol import resolve

REPO_ROOT = Path(__file__).resolve().parents[3]
SHELL_NAMES = ("workbench", "course-student", "course-teacher")


def _category(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        resource_pack=kw.get("resource_pack", {}),
        conditions=kw.get("conditions", []),
        default_role=kw.get("default_role"),
        shell=kw.get("shell"),
    )


def _task(override=None) -> SimpleNamespace:
    return SimpleNamespace(protocol_override=override)


def _project(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        id="p1",
        settings=kw.get("settings"),
        external_task_id=kw.get("external_task_id"),
    )


def test_undeclared_falls_back_to_default() -> None:
    """No 项目集, no 赛题, no settings — which is every project that exists today."""
    got = resolve_shell(project=_project())
    assert got.name == DEFAULT_SHELL_NAME


def test_a_project_inherits_its_category_shell() -> None:
    got = resolve_shell(
        project=_project(),
        task=_task(),
        category=_category(shell="course-student"),
    )
    assert got.name == "course-student"


def test_a_task_override_replaces_the_whole_shell_key() -> None:
    """Whole-key replacement, not a deep merge — the protocol's rule, not a new one.

    A 壳 has four items; a half-inherited one (this 项目集's home screen with
    that 赛题's navigation) is a 壳 nobody wrote and nobody can reason about.
    """
    got = resolve_shell(
        project=_project(),
        task=_task({"shell": "workbench"}),
        category=_category(shell="course-student"),
    )
    assert got.name == "workbench"
    # And it is the WHOLE declaration that came across, not a merge of the two.
    assert got.terms == CATALOG["workbench"].terms
    assert got.hidden == CATALOG["workbench"].hidden


def test_a_projects_own_setting_outranks_the_protocol() -> None:
    """The one level a person sets on the thing they are looking at wins.

    A 项目集 is an institution's default for its twenty 赛题, not a veto over a
    project that has said what it wants.
    """
    got = resolve_shell(
        project=_project(settings={"shell": "course-teacher"}),
        task=_task({"shell": "workbench"}),
        category=_category(shell="course-student"),
    )
    assert got.name == "course-teacher"


def test_a_project_setting_also_outranks_a_silent_protocol() -> None:
    got = resolve_shell(
        project=_project(settings={"shell": "workbench"}),
        task=_task(),
        category=_category(),
    )
    assert got.name == "workbench"


def test_an_empty_project_setting_does_not_shadow_the_protocol() -> None:
    """`{}` and a `None` value mean 「nobody said」, not 「the default, loudly」.

    Otherwise a project that once had the key cleared would stop inheriting its
    项目集's 壳, and the fix would be to look for a value that is not there.
    """
    for settings in ({}, {"shell": None}, {"shell": ""}):
        got = resolve_shell(
            project=_project(settings=settings),
            task=_task(),
            category=_category(shell="workbench"),
        )
        assert got.name == "workbench", settings


def test_the_protocol_carries_the_shell_key() -> None:
    """壳 rides the 机构协议 chain rather than a second one of its own."""
    got = resolve(category=_category(shell="course-student"), task=_task())
    assert got.shell == "course-student"


def test_an_unknown_shell_name_falls_back_to_default(caplog) -> None:
    """A half-rolled-out name or a hand-edited row must not break navigation."""
    got = resolve_shell(
        project=_project(), task=_task(), category=_category(shell="课改实验班")
    )
    assert got.name == DEFAULT_SHELL_NAME
    assert "unknown shell" in caplog.text


def test_default_is_todays_interface_verbatim() -> None:
    """The parity contract, written down where it can fail.

    A project that declares no 壳 has to be indistinguishable from the product
    before 壳 existed. If someone reorders the sidebar for everyone by editing
    this declaration, this is the test that says so.
    """
    default = CATALOG[DEFAULT_SHELL_NAME]
    assert default.home == "workspace-running"
    assert default.hidden == ()
    assert default.terms == {}
    assert default.nav.rail == ("home", "projects", "add")
    assert default.nav.tabs == ("spaces", "workspace", "inbox")
    assert default.nav.project == (
        "overview",
        "workspace-running",
        "calendar",
        "project-library",
        "project-delivery",
        "project-agents",
        "project-members",
    )


def test_every_shell_names_only_known_keys() -> None:
    """A typo in a declaration hides a destination with no error anywhere."""
    known_rail = {"home", "projects", "add"}
    known_tabs = {"spaces", "workspace", "inbox"}
    # The project pages a 壳 may name — the route names of `workspaceRoutes.ts`.
    known_project = {
        "overview",
        "workspace-running",
        "calendar",
        "project-library",
        "project-delivery",
        "project-agents",
        "project-members",
    }
    for name, shell in CATALOG.items():
        assert set(shell.nav.rail) <= known_rail, name
        assert set(shell.nav.tabs) <= known_tabs, name
        assert set(shell.nav.project) <= known_project, name
        assert set(shell.hidden) <= known_project, name
        # A 壳 that renamed a noun to nothing would render an empty label.
        assert all(shell.terms.values()), name


def test_a_shell_shows_every_key_it_names_at_most_once() -> None:
    for name, shell in CATALOG.items():
        for surface in (shell.nav.rail, shell.nav.tabs, shell.nav.project):
            assert len(set(surface)) == len(surface), (name, surface)


def test_adding_a_shell_touches_no_component() -> None:
    """「新增一个壳只改声明、不改组件」, enforced rather than promised.

    Every 壳 is four fields of data with no behaviour, so a component that
    mentioned one by name would mean a 壳 had grown an `if` — and that is the
    road to 「比两套前端更难维护」, which is the whole reason 壳 is data. The
    frontend therefore must not know any 壳's name: it receives the resolved
    declaration over the API and renders it.
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "frontend" / "src").rglob("*"):
        if path.suffix not in {".ts", ".vue", ".js"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for name in SHELL_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} mentions {name!r}")
    assert not offenders, offenders
