"""Private display metadata for authenticated API responses only.

The query itself lives in the user domain (``domain.user.services``): a remark
is a user-domain fact, and the domain's own author cards read it too. This
module stays as the import the existing routes use.
"""

from app.domain.user.services import remarks_for_viewer

__all__ = ["remarks_for_viewer"]
