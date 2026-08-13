"""Central import of every ORM model.

Importing this module ensures all tables register on ``Base.metadata`` — used by
Alembic autogenerate, the test schema creator, and anywhere that needs the full
metadata. Keep this list complete when adding a new model.
"""

from app.domain.alert import models as alert  # noqa: F401
from app.domain.answers import models as answers  # noqa: F401
from app.domain.attachment import models as attachment  # noqa: F401
from app.domain.avatars import models as avatars  # noqa: F401
from app.domain.block import models as block  # noqa: F401
from app.domain.comments import models as comments  # noqa: F401
from app.domain.conclusion import models as conclusion  # noqa: F401
from app.domain.cx_task import models as cx_task  # noqa: F401
from app.domain.device import models as device  # noqa: F401
from app.domain.discussion import models as discussion  # noqa: F401
from app.domain.expert_role import models as expert_role  # noqa: F401
from app.domain.groups import models as groups  # noqa: F401
from app.domain.idempotency import models as idempotency  # noqa: F401
from app.domain.identity import models as identity  # noqa: F401
from app.domain.knowledge import models as knowledge  # noqa: F401
from app.domain.llm import models as llm  # noqa: F401
from app.domain.machine import models as machine  # noqa: F401
from app.domain.materials import models as materials  # noqa: F401
from app.domain.memory import models as memory  # noqa: F401
from app.domain.milestone import models as milestone  # noqa: F401
from app.domain.notification import models as notification  # noqa: F401
from app.domain.oauth import models as oauth  # noqa: F401
from app.domain.passkey import models as passkey  # noqa: F401
from app.domain.project import models as project  # noqa: F401
from app.domain.questions import models as questions  # noqa: F401
from app.domain.review import models as review  # noqa: F401
from app.domain.space import models as space  # noqa: F401
from app.domain.tag import models as tag  # noqa: F401
from app.domain.task import models as task  # noqa: F401
from app.domain.team import models as team  # noqa: F401
from app.domain.team_project import models as team_project  # noqa: F401
from app.domain.topic import models as topic  # noqa: F401
from app.domain.usage import models as usage  # noqa: F401
from app.domain.user import models as user  # noqa: F401
from app.domain.webhook import models as webhook  # noqa: F401
