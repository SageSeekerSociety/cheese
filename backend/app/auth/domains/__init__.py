from app.auth.domains.question import register_question_permissions
from app.auth.domains.task import register_task_permissions


def register_all_permissions() -> None:
    """Wire every domain's permission configs + role providers into the shared
    permission_checker. MUST run once at startup: without it the checker has no
    role providers registered, so ``get_domain_roles`` returns an empty set for
    every domain and EVERY ``require_permission``-gated endpoint responds 403
    (task/question admin actions, …).
    """
    register_question_permissions()
    register_task_permissions()


__all__ = [
    "register_all_permissions",
    "register_question_permissions",
    "register_task_permissions",
]
