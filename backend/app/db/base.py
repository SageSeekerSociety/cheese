"""Import all model modules so Base.metadata is fully populated for Alembic."""

from app.agent import models as _agent_models  # noqa: F401
from app.db.base_class import Base  # noqa: F401
from app.domain.answers import models as _answers  # noqa: F401
from app.domain.attachment import models as _attachment  # noqa: F401
from app.domain.avatars import models as _avatars  # noqa: F401
from app.domain.block import models as _block  # noqa: F401
from app.domain.comments import models as _comments  # noqa: F401
from app.domain.device import models as _device  # noqa: F401
from app.domain.discussion import models as _discussion  # noqa: F401
from app.domain.groups import models as _groups  # noqa: F401
from app.domain.invite import models as _invite  # noqa: F401
from app.domain.knowledge import models as _knowledge  # noqa: F401
from app.domain.llm import call_logger as _call_logger  # noqa: F401
from app.domain.llm import models as _llm  # noqa: F401
from app.domain.materials import models as _materials  # noqa: F401
from app.domain.notification import models as _notification  # noqa: F401
from app.domain.oauth import models as _oauth  # noqa: F401
from app.domain.passkey import models as _passkey  # noqa: F401
from app.domain.project import models as _project  # noqa: F401
from app.domain.questions import models as _questions  # noqa: F401
from app.domain.space import models as _space  # noqa: F401
from app.domain.task import models as _task  # noqa: F401
from app.domain.team import models as _team  # noqa: F401
from app.domain.thread import models as _thread  # noqa: F401
from app.domain.topics import models as _topics  # noqa: F401
from app.domain.user import models as _user  # noqa: F401
