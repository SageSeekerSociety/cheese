"""壳 (shell) — the four declaration-only dials that make one platform look like
several products without forking it.

**Why this exists.** The platform has exactly one entry today: everybody who
opens any project gets the same interface, and the desktop first screen is
always 看板. 办公 and 课程 are the same base with a different 壳, and this module
is the shared ground they stand on.

**A 壳 may only open, close, reorder and rename. It may never add a capability,
and no 壳 holds code.** The moment a 壳 needs an `if` it has become a second
frontend, which is strictly worse than the two frontends it replaced — so the
answer to 「我这个场景就是长得不一样」 is a new PLATFORM capability that every 壳
can then expose, never a private door for one scene.

**Where a 壳 is declared.** The same place the 机构协议 lives, with the same
inheritance: the 项目集 (`space_categories.shell`) writes it, a single 赛题
overrides the whole key (`task.protocol_override["shell"]`), and a project's own
`Project.settings["shell"]` outranks both. Undeclared falls back to `default`.
That chain is `app.domain.task.protocol` — this module does NOT grow a second
one; it only turns the resolved NAME into the declaration below.

**Adding a fifth 壳 is a declaration.** Append one `Shell(...)` to `CATALOG` and
point a 项目集 at its name. No component changes, and
`test_adding_a_shell_touches_no_component` is what keeps that true.
"""

from dataclasses import dataclass, field

#: The 壳 nobody has to declare: what every project got before 壳 existed.
DEFAULT_SHELL_NAME = "default"


@dataclass(frozen=True)
class Nav:
    """Which cells each navigation surface shows, and in what order.

    Three lists rather than one, because the surfaces genuinely hold different
    things (a rail of project tiles vs. a three-cell bottom bar vs. a project's
    inner pages) — see `frontend/src/components/common/Navigation/destinations.ts`,
    which argues the same point about the two APP-level lists. A single list
    plus filters is what made 「＋新建项目」 homeless on mobile once already.

    Keys not listed here are not deleted: the frontend lifts every page a 壳
    does not show into 「更多」, so 默认收起 stays 收起 and never becomes 禁止.
    """

    #: App rail (desktop): "home" | "projects" | "add".
    rail: tuple[str, ...]
    #: App bottom bar (mobile): "spaces" | "workspace" | "inbox".
    tabs: tuple[str, ...]
    #: Project sidebar, by route name (`workspaceRoutes.ts`), "全局" excluded.
    project: tuple[str, ...]


@dataclass(frozen=True)
class Shell:
    """One declaration. Data only — nothing in here branches."""

    name: str
    #: The workspace's first screen on desktop, by route name. `None` means
    #: 「do not move」 — the address itself is the destination.
    home: str | None
    nav: Nav
    #: Project pages collapsed under 「更多」 by default. A subset of `nav.project`
    #: is the normal shape, but the frontend shows EVERY page it is not
    #: currently rendering under 更多, so a page left out of `nav.project` is
    #: one click away too, never gone.
    hidden: tuple[str, ...] = ()
    #: 词表: the shell's nouns, by term key. `{"project": "工作"}` says this shell
    #: calls a 项目 a 「工作」. Strings interpolate the term (`新建{project}`), so
    #: the word AND its grammar stay in the translation catalog rather than in a
    #: substitution this module would have to get right.
    terms: dict[str, str] = field(default_factory=dict)


#: 现状, verbatim: the desktop first screen is 看板 and both app-level lists are in
#: their existing order. A project that declares no 壳 must be indistinguishable
#: from today, screen by screen — that equality is the acceptance test for this
#: whole mechanism, which is why this declaration is edited whenever 「today」
#: moves upstream. It moved twice already: #1330/#1339 narrowed the project
#: sidebar to 资料库 and sent 日历 / 成员 into the ⋯ menu beside the project name
#: (总览 and 导出与发布 stopped being pages at all), and then 成员 came back out
#: — 收起名册 put 「退出项目」 (which lives on that page) two menus deep past the
#: one ⋯ people already miss, so nobody could find how to leave. 日历 stays in
#: the menu: a schedule is still a 一年点几次 page. `hidden` carries these moves
#: rather than a list of seven, so what stays hidden is one click away in the
#: menu — still 收起, never 禁止. Un-hiding is 「open」, the dial a 壳 has.
#:
#: 看板 is the 壳's `home` and lives in no `nav.project` list: the project name
#: itself is the way to it, exactly as upstream argues beside the menu it draws.
_DEFAULT = Shell(
    name=DEFAULT_SHELL_NAME,
    home="workspace-running",
    nav=Nav(
        rail=("home", "projects", "add"),
        tabs=("spaces", "workspace", "inbox"),
        project=(
            "calendar",
            "project-library",
            "project-routines",
            "project-members",
            "project-skills",
        ),
    ),
    hidden=("calendar", "project-routines", "project-skills"),
)

#: 办公: a project is a 工作, a topic is an 议题, and the day starts in 工作区.
#: 日历 is collapsed — a team workspace does not put one person's schedule in
#: everyone's sidebar — but it stays one click away.
_WORKBENCH = Shell(
    name="workbench",
    home="workspace-running",
    nav=Nav(
        rail=("home", "projects", "add"),
        tabs=("workspace", "spaces", "inbox"),
        project=(
            "calendar",
            "project-library",
            "project-routines",
            "workspace-running",
            "project-members",
            "project-skills",
        ),
    ),
    hidden=("calendar",),
    terms={"project": "工作", "topic": "议题"},
)

#: 课程 (学生): first screen is 看板 for now — the course template's real first
#: screen (本周任务, or the 助教 conversation in an 答疑 course) is a page this
#: build does not have yet, and `home` may only name a route that exists. When
#: that page lands, this one line moves and nothing else does. 提问 is the noun
#: students are actually taught, so 话题 reads as 提问 here.
_COURSE_STUDENT = Shell(
    name="course-student",
    home="workspace-running",
    nav=Nav(
        rail=("home", "projects", "add"),
        tabs=("workspace", "inbox", "spaces"),
        project=(
            "project-library",
            "calendar",
            "project-members",
            "workspace-running",
            "project-routines",
        ),
    ),
    hidden=("calendar", "project-members", "project-routines"),
    terms={"project": "课程", "topic": "提问"},
)

#: 课程 (老师): the board first — a teacher's question is 「这个班现在有什么在等
#: 我」, which is exactly the column the board sorts by. 名册 second.
_COURSE_TEACHER = Shell(
    name="course-teacher",
    home="workspace-running",
    nav=Nav(
        rail=("home", "projects", "add"),
        tabs=("workspace", "inbox", "spaces"),
        project=(
            "workspace-running",
            "project-members",
            "project-library",
            "calendar",
            "project-routines",
        ),
    ),
    hidden=("project-library", "project-routines"),
    terms={"project": "课程", "topic": "提问"},
)

#: The built-in 壳. A name is what gets declared on a 项目集 / 赛题 / project; the
#: declaration itself lives here, once, so twenty 赛题 in one 项目集 share it.
CATALOG: dict[str, Shell] = {
    shell.name: shell
    for shell in (_DEFAULT, _WORKBENCH, _COURSE_STUDENT, _COURSE_TEACHER)
}

#: The 壳 a newly created 题目板's default category declares — a 题目板 *is* a
#: course now, so a new one opens as the course template instead of a blank
#: board. This is the name only; `SpaceService.create_space` is what writes it.
#:
#: It is a DEFAULT and nothing more: it rides the one chain in
#: `app.domain.task.protocol`, so a single 题目 may replace it and a project's own
#: `settings["shell"]` still outranks both. A board created before this existed
#: declares nothing and keeps rendering `default` — the fallback is untouched.
#:
#: Student rather than teacher 壳: a 题目板's 壳 reaches the *projects* under it
#: (a student's project inherits it through the 题目 it linked). The teacher's
#: course views are the 题目板's own management screens, which are not projects
#: and consume no 壳 today — so there is no second declaration to make here, and
#: making one would be the second chain this module exists to avoid.
DEFAULT_CATEGORY_SHELL_NAME: str = _COURSE_STUDENT.name

#: The 壳 names that mean 「the 题目板 declaring one is a course」.
#:
#: A 题目板's own screens are not projects and so read no 壳 — but a board whose
#: default 分组 declares a course 壳 *is* a course, and its screens need to know
#: that before they can decide anything (see `SpaceOut.isCourse`). Both names are
#: here because either says the same thing about the board; which 壳 a *project*
#: under it inherits stays a question for the chain in
#: `app.domain.task.protocol`.
COURSE_SHELL_NAMES: frozenset[str] = frozenset(
    {_COURSE_STUDENT.name, _COURSE_TEACHER.name}
)


def is_course_shell(name: str | None) -> bool:
    """Whether this 壳 name says its 题目板 is a course. Undeclared is not."""
    return bool(name) and name in COURSE_SHELL_NAMES


def lookup(name: str | None) -> Shell:
    """The declaration for ``name``, or `default`.

    Unknown names resolve to `default` rather than raising: a 项目集 naming a 壳
    this build does not have (a rollout half-done, a typo in a hand-edited row)
    must leave every project that reads it working. Silently, though, would be
    the other failure — a teacher picking 「course-student」 and getting the
    default 壳 with no explanation — so the caller logs the miss.
    """
    if not name:
        return CATALOG[DEFAULT_SHELL_NAME]
    return CATALOG.get(name) or CATALOG[DEFAULT_SHELL_NAME]


def is_known(name: str | None) -> bool:
    """Whether the name names a 壳 this build declares. `default` counts."""
    return bool(name) and name in CATALOG
