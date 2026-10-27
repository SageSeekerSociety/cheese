"""What each role in a project may do (#3830).

A project has one owner, and everyone else in it holds one of three roles:
admin, member, mentor. The owner holds every permission a person can hold
(:data:`PEOPLE_PERMISSIONS`) and is not in the table below; each role is a set of the permissions listed here, defaulting to
:data:`DEFAULT_GRANTS`, and a project may change a role's set (its
``project_role_grants`` row). The two locked permissions cannot be changed: an
admin always manages people and roles and nobody else ever does, so a project
can never lose everyone able to manage it.

The keys and their defaults are the frontend's ``lib/projectPermissions.ts``,
name for name.

What the owner alone does is not a permission: handing the project over,
moving a personal project into a team, archiving it.

A few permissions are an AI teammate's alone (:data:`AGENT_ONLY`): what the
``cheese`` CLI does from inside a conversation it works in. No person's seat
holds them, a role cannot be granted them, and the table does not list them.
"""

import enum


class Role(enum.StrEnum):
    admin = "admin"
    member = "member"
    mentor = "mentor"


class Seat(enum.StrEnum):
    """Where a person sits in a project: a role, or above them all."""

    owner = "owner"
    admin = "admin"
    member = "member"
    mentor = "mentor"


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
    #: Leave a note for its own session working another thread.
    note_own_threads = "note_own_threads"
    #: Mint the conversation's webhook credential, for a caller outside to wake it.
    mint_webhook_token = "mint_webhook_token"
    #: Put a status line from the machine it works on into the conversation's 现场.
    record_machine_status = "record_machine_status"


#: What an AI teammate does from inside a conversation it works in, and no
#: person does: nobody's seat holds these and no role can be granted them.
AGENT_ONLY: frozenset[Permission] = frozenset(
    {
        Permission.note_own_threads,
        Permission.mint_webhook_token,
        Permission.record_machine_status,
    }
)

#: Everything a person's seat can hold; the owner holds all of it.
PEOPLE_PERMISSIONS: frozenset[Permission] = frozenset(Permission) - AGENT_ONLY


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

#: The permissions a role is made of, in the order the table lists them.
PERMISSION_ORDER: tuple[Permission, ...] = tuple(
    p for p in Permission if p in PEOPLE_PERMISSIONS
)

LOCKED: frozenset[Permission] = frozenset(
    {Permission.manage_members, Permission.change_roles}
)

DEFAULT_GRANTS: dict[Role, frozenset[Permission]] = {
    Role.admin: PEOPLE_PERMISSIONS,
    Role.member: frozenset(
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
    Role.mentor: frozenset(
        {Permission.speak, Permission.ask_agent, Permission.comment}
    ),
}


def with_locks(role: Role, granted: frozenset[Permission]) -> frozenset[Permission]:
    """A role's set with the locked permissions where they always are, and
    nothing a person cannot hold."""
    granted = granted & PEOPLE_PERMISSIONS
    if role is Role.admin:
        return granted | LOCKED
    return granted - LOCKED


#: Higher first. A person manages only those strictly below their own seat.
RANK: dict[Seat, int] = {Seat.owner: 3, Seat.admin: 2, Seat.member: 1, Seat.mentor: 1}
