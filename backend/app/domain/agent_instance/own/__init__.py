"""A member's own coding agent, as a teammate in a project (#2991).

The row that makes a project's agent someone's (`OwnAgent`) is read in many
places — where its session runs, what it may be charged, whose assistant it is — and
every one of them asks the same question: is the agent acting here somebody's
own, and whose. This is that one answer.

**This package is that answer, split by responsibility.** One file held all of it
until 2026-10-16; the bodies moved into the sibling modules of this package:

  * ``connections`` — a person's model connections, and which one an agent runs
    on (`Login`, `logins`, `connection_of`, `label`);
  * ``settings`` — the project settings that let members bring their own agents
    in, and which kinds and hosts they allow;
  * ``ownership`` — whose own agent a row is (`Owned`, `owned_by_seat`,
    `owned_by_session`, `owned_instance`, `owned_in_project`, `owner_of`);
  * ``naming`` — what one is called and what it is told it is;
  * ``membership`` — bringing one in, taking it out, whose assistant it is;
  * ``admission`` — whether its connection may be used here, with the sentence
    the room is told when it may not;
  * ``views`` — what the API lists.

**What stays here is the front door.** ``app.domain.agent_instance.own`` is the
path the tree imports these names by, and a missing name is an ImportError at
load time, not at call time — the caller cannot even start. So every name the
module used to answer with is re-exported below as an explicit `X as X`: not a
second definition, but the same object under the path that always named it.
This is `review/services`' shape.

The submodules only import downward — ``connections`` below everything,
``ownership`` and ``settings`` above it, then ``naming``, then ``membership``
and ``admission``, then ``views`` — so nothing here is a cycle. They import each
other directly rather than through this module, which is what keeps an import in
the package from re-entering it.
"""

from app.domain.agent_instance.own.admission import unusable as unusable
from app.domain.agent_instance.own.connections import Login as Login
from app.domain.agent_instance.own.connections import _connection_key as _connection_key
from app.domain.agent_instance.own.connections import _held as _held
from app.domain.agent_instance.own.connections import _login as _login
from app.domain.agent_instance.own.connections import _usable as _usable
from app.domain.agent_instance.own.connections import label as label
from app.domain.agent_instance.own.connections import logins as logins
from app.domain.agent_instance.own.membership import add as add
from app.domain.agent_instance.own.membership import remove as remove
from app.domain.agent_instance.own.naming import (
    _name_owners_agents as _name_owners_agents,
)
from app.domain.agent_instance.own.naming import current_role as current_role
from app.domain.agent_instance.own.naming import own_handle as own_handle
from app.domain.agent_instance.own.naming import own_name as own_name
from app.domain.agent_instance.own.naming import own_role as own_role
from app.domain.agent_instance.own.naming import (
    rename_for_connection as rename_for_connection,
)
from app.domain.agent_instance.own.ownership import _OWNED as _OWNED
from app.domain.agent_instance.own.ownership import Owned as Owned
from app.domain.agent_instance.own.ownership import _nickname as _nickname
from app.domain.agent_instance.own.ownership import _nickname_of as _nickname_of
from app.domain.agent_instance.own.ownership import connection_of as connection_of
from app.domain.agent_instance.own.ownership import owned_by_seat as owned_by_seat
from app.domain.agent_instance.own.ownership import (
    owned_by_session as owned_by_session,
)
from app.domain.agent_instance.own.ownership import (
    owned_in_project as owned_in_project,
)
from app.domain.agent_instance.own.ownership import owned_instance as owned_instance
from app.domain.agent_instance.own.ownership import owner_of as owner_of
from app.domain.agent_instance.own.views import listing as listing
from app.domain.agent_instance.own.views import my_connections as my_connections
