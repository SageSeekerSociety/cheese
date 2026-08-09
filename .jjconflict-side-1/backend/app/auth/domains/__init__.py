from app.auth.domains.knowledge import register_knowledge_permissions
from app.auth.domains.question import register_question_permissions
from app.auth.domains.space import register_space_permissions
from app.auth.domains.task import register_task_permissions
from app.auth.domains.team import register_team_permissions


def register_all_permissions() -> None:
    """Wire every domain's permission configs + role providers into the shared
    permission_checker. MUST run once at startup: without it the checker has no
    role providers registered, so ``get_domain_roles`` returns an empty set for
    every domain and EVERY ``require_permission``-gated endpoint responds 403
    (team invite/edit/delete, space/task/knowledge/question admin actions, …).
    """
    register_knowledge_permissions()
    register_question_permissions()
    register_space_permissions()
    register_task_permissions()
    register_team_permissions()


__all__ = [
    "register_all_permissions",
    "register_knowledge_permissions",
    "register_question_permissions",
    "register_space_permissions",
    "register_task_permissions",
    "register_team_permissions",
]
