"""A member's own coding agent, as a teammate in a project (#2991).

The row that makes a project's agent someone's (`OwnAgent`) is read in many
places — where its session runs, what it may be charged, who may call it — and
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
  * ``membership`` — bringing one in, taking it out, who may call it;
  * ``admission`` — whether it may be used here, and by whom, with the sentence
    the room is told when it may not;
  * ``views`` — what the API lists.

**What stays here is the front door.** ``app.domain.agent_instance.own`` is the
path the tree imports these names by, and a missing name is an ImportError at
load time, not at call time — the caller cannot even start. So every name the
module used to answer with is re-exported below as an explicit `X as X`: not a
second definition, but the same object under the path that always named it.
This is `review/services`' shape; the guard is
``tests/unit/test_own_module_exports_what_the_tree_imports.py``.

The submodules only import downward — ``connections`` below everything,
``ownership`` and ``settings`` above it, then ``naming``, then ``membership``
and ``admission``, then ``views`` — so nothing here is a cycle. They import each
other directly rather than through this module, which is what keeps an import in
the package from re-entering it.
"""

from app.domain.agent_instance.models import OwnCallers as OwnCallers
from app.domain.agent_instance.own.admission import callable_by as callable_by
from app.domain.agent_instance.own.admission import may_chat_with as may_chat_with
from app.domain.agent_instance.own.admission import may_work_for as may_work_for
from app.domain.agent_instance.own.admission import unusable as unusable
from app.domain.agent_instance.own.admission import why_not as why_not
from app.domain.agent_instance.own.connections import Login as Login
from app.domain.agent_instance.own.connections import _connection_key as _connection_key
from app.domain.agent_instance.own.connections import _held as _held
from app.domain.agent_instance.own.connections import _login as _login
from app.domain.agent_instance.own.connections import _usable as _usable
from app.domain.agent_instance.own.connections import label as label
from app.domain.agent_instance.own.connections import logins as logins
from app.domain.agent_instance.own.membership import add as add
from app.domain.agent_instance.own.membership import remove as remove
from app.domain.agent_instance.own.membership import set_callers as set_callers
from app.domain.agent_instance.own.naming import (
    _name_owners_agents as _name_owners_agents,
)
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
from app.domain.agent_instance.own.settings import _KIND_WORDS as _KIND_WORDS
from app.domain.agent_instance.own.settings import (
    HOSTS_SETTING as HOSTS_SETTING,
)
from app.domain.agent_instance.own.settings import KINDS_SETTING as KINDS_SETTING
from app.domain.agent_instance.own.settings import SETTING as SETTING
from app.domain.agent_instance.own.settings import allowed_hosts as allowed_hosts
from app.domain.agent_instance.own.settings import allowed_kinds as allowed_kinds
from app.domain.agent_instance.own.settings import allows as allows
from app.domain.agent_instance.own.settings import (
    allows_own_agents as allows_own_agents,
)
from app.domain.agent_instance.own.settings import host_refused as host_refused
from app.domain.agent_instance.own.settings import (
    kinds_refused_sentence as kinds_refused_sentence,
)
from app.domain.agent_instance.own.views import listing as listing
from app.domain.agent_instance.own.views import my_connections as my_connections
