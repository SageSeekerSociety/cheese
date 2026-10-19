"""Team model connections instantiated as project coding teammates.

The member who added a teammate is retained for its prompt and naming.
Connection access follows the project's team and selected model.
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
