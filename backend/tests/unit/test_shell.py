"""壳 (shell) — same inheritance as the 机构协议, one catalog, no forks.

What these pin is the chain and its precedence, because getting the order wrong
is silent: a project that should run 课程 quietly runs 现状 instead, and the only
symptom is a teacher saying 「怎么还是老样子」. The three levels are
Project.settings → 赛题 override → 项目集 → `default`, and undeclared must land on
`default`, which is today's interface screen for screen.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.domain.shell.catalog import CATALOG, DEFAULT_SHELL_NAME
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
    as it is. If someone reorders the sidebar for everyone by editing this
    declaration, this is the test that says so.

    「今天」 is not frozen: main moved it on 2026-09-21 (#1330/#1339 narrowed the
    project sidebar to 资料库, sent 日历/成员 into the ⋯ menu and retired 总览 /
    导出与发布 as pages), and this declaration moved with it. What must not move
    is a project that declares nothing seeing something other than what a
    project without 壳 sees today.
    """
    default = CATALOG[DEFAULT_SHELL_NAME]
    assert default.home == "workspace-running"
    # 侧栏那一面：资料库是常驻那一格，日历与名册默认收进项目名旁边那个 ⋯ 菜单。
    # 看板不在里面 —— 它就是 home，项目名那一行点下去就到。
    assert default.nav.project == ("calendar", "project-library", "project-members")
    assert default.hidden == ("calendar", "project-members")
    assert default.terms == {}
    assert default.nav.rail == ("home", "projects", "add")
    assert default.nav.tabs == ("spaces", "workspace", "inbox")


def test_every_shell_names_only_known_keys() -> None:
    """A typo in a declaration hides a destination with no error anywhere."""
    known_rail = {"home", "projects", "add"}
    known_tabs = {"spaces", "workspace", "inbox"}
    # The project pages a 壳 may name — the route names of `workspaceRoutes.ts`.
    known_project = {
        "workspace-running",
        "calendar",
        "project-library",
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

    测试桩不算：``Shell.name`` 是必填字段，一份构造壳的用例总得给它起个名字，那不是
    组件里长出来的 ``if``。这条守的是**渲染代码**，所以扫的时候跳过 ``.spec`` /
    ``.test``——生产代码那半一分不减。
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "frontend" / "src").rglob("*"):
        if path.suffix not in {".ts", ".vue", ".js"} or not path.is_file():
            continue
        if ".spec." in path.name or ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for name in SHELL_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} mentions {name!r}")
    assert not offenders, offenders


def test_the_frontends_fallback_is_the_backend_default() -> None:
    """前端那份兜底拷贝必须和 `CATALOG["default"]` 逐字段相等。

    前端只有一份壳的拷贝 (`frontend/src/lib/default-shell.json`)，它存在的原因是
    「一个项目都读不到的时候导航仍然要画出来」——首页、空间页、还没进项目的桌面
    rail。那些地方没有项目行，也就没有服务端解析好的壳，而答复只能是 default。

    它是拷贝，所以会分叉；分叉了不会报错，只会让两类屏幕悄悄长得不一样。这条用例
    就是那声该响的响：改 catalog 里 default 的任何一格，这里立刻红。
    """
    raw = json.loads(
        (REPO_ROOT / "frontend" / "src" / "lib" / "default-shell.json").read_text(
            encoding="utf-8"
        )
    )
    default = CATALOG[DEFAULT_SHELL_NAME]
    # 形状先对齐：多一个键少一个键都是分叉，哪怕值是对的。
    assert set(raw) == {"name", "home", "nav", "hidden", "terms"}
    assert set(raw["nav"]) == {"rail", "tabs", "project"}
    assert raw == {
        "name": default.name,
        "home": default.home,
        "nav": {
            "rail": list(default.nav.rail),
            "tabs": list(default.nav.tabs),
            "project": list(default.nav.project),
        },
        "hidden": list(default.hidden),
        "terms": dict(default.terms),
    }
    # default 不换任何词。
    assert raw["terms"] == {}
