"""What each role in a project may do (#3830, #4795).

A project has one owner, and everyone else in it holds one role. Two roles are
built in, admin and member, with their names fixed everywhere and their
permissions defaulting to :data:`DEFAULT_GRANTS`; a project may change those
permissions (a ``project_role_defs`` row for the built-in role). Any other role
is the project's own: a name and a set of permissions, made by copying another
role or from a template (:class:`Template`) the platform knows by its mark
rather than its name. The two locked permissions cannot be changed: an admin
always manages people and roles and nobody else ever does, so a project can
never lose everyone able to manage it.

The keys and their defaults are the frontend's ``lib/projectPermissions.ts``,
name for name.

What the owner alone does is not a permission: handing the project over,
moving a personal project into a team, archiving it.
"""

import enum


class Builtin(enum.StrEnum):
    """The two roles every project has. Their key is their name."""

    admin = "admin"
    member = "member"


class Template(enum.StrEnum):
    """A role the platform makes on demand and recognises by this mark: a
    project may rename it and change what it may do."""

    mentor = "mentor"


class Seat(enum.StrEnum):
    """How high someone sits in a project: the owner, an admin, or anyone
    holding another role. A person manages only those strictly below."""

    owner = "owner"
    admin = "admin"
    member = "member"


class Permission(enum.StrEnum):
    #: Invite people into the project and take them out of it.
    manage_members = "manage_members"
    #: Make people members or mentors, only below oneself; only the owner makes
    #: someone an admin.
    change_roles = "change_roles"
    #: The project's settings: address, default model, environment, repository
    #: and merge rules, the project site, importing skills, members' own agents.
    edit_settings = "edit_settings"
    export_project = "export_project"
    #: Create public or private channels and bring people into ones you are in.
    create_channels = "create_channels"
    #: Change, archive and delete any channel without being its admin, and step
    #: into a private one.
    manage_channels = "manage_channels"
    speak = "speak"
    #: Ask the project's AI teammates for an answer, a document, a piece of work.
    ask_agent = "ask_agent"
    #: Create tasks, review what they hand back, and reopen closed ones.
    tasks = "tasks"
    #: Create, change and delete documents and files, and save them to the library.
    edit_content = "edit_content"
    #: Make and take back a document's read-only link, which anyone holding it
    #: reads without being in the project.
    share_documents = "share_documents"
    #: Comment on documents and suggest changes to their text.
    comment = "comment"
    #: Add and retire AI teammates; their skills, MCP servers and shared memory.
    configure_agents = "configure_agents"


#: What manages people, the project's settings or every channel — never done
#: by something running unattended on a person's behalf.
MANAGING: frozenset[Permission] = frozenset(
    {
        Permission.manage_members,
        Permission.change_roles,
        Permission.edit_settings,
        Permission.manage_channels,
    }
)

#: The order the table lists them in.
PERMISSION_ORDER: tuple[Permission, ...] = tuple(Permission)

LOCKED: frozenset[Permission] = frozenset(
    {Permission.manage_members, Permission.change_roles}
)

DEFAULT_GRANTS: dict[Builtin, frozenset[Permission]] = {
    Builtin.admin: frozenset(Permission),
    Builtin.member: frozenset(
        {
            Permission.export_project,
            Permission.create_channels,
            Permission.speak,
            Permission.ask_agent,
            Permission.tasks,
            Permission.edit_content,
            Permission.share_documents,
            Permission.comment,
            Permission.configure_agents,
        }
    ),
}

#: What a role made from a template may do when it is made.
TEMPLATE_GRANTS: dict[Template, frozenset[Permission]] = {
    Template.mentor: frozenset(
        {Permission.speak, Permission.ask_agent, Permission.comment}
    ),
}


def with_locks(admin: bool, granted: frozenset[Permission]) -> frozenset[Permission]:
    """A role's set with the locked permissions where they always are."""
    if admin:
        return granted | LOCKED
    return granted - LOCKED


#: Higher first. A person manages only those strictly below their own seat.
RANK: dict[Seat, int] = {Seat.owner: 3, Seat.admin: 2, Seat.member: 1}
