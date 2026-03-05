from app.auth.domains.knowledge import register_knowledge_permissions
from app.auth.domains.question import register_question_permissions
from app.auth.domains.space import register_space_permissions
from app.auth.domains.task import register_task_permissions
from app.auth.domains.team import register_team_permissions

__all__ = [
    "register_knowledge_permissions",
    "register_question_permissions",
    "register_space_permissions",
    "register_task_permissions",
    "register_team_permissions",
]
