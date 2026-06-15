"""Central import of every ORM model.

Importing this module ensures all tables register on ``Base.metadata`` — used by
Alembic autogenerate, the test schema creator, and anywhere that needs the full
metadata. Keep this list complete when adding a new model.
"""

from app.domain.block import models as block  # noqa: F401
from app.domain.expert_role import models as expert_role  # noqa: F401
from app.domain.memory import models as memory  # noqa: F401
from app.domain.milestone import models as milestone  # noqa: F401
from app.domain.notification import models as notification  # noqa: F401
from app.domain.project import models as project  # noqa: F401
from app.domain.review import models as review  # noqa: F401
from app.domain.space import models as space  # noqa: F401
from app.domain.task import models as task  # noqa: F401
from app.domain.topic import models as topic  # noqa: F401
from app.domain.usage import models as usage  # noqa: F401
from app.domain.user import models as user  # noqa: F401
