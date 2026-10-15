"""门面守卫：``app.domain.feedback.services`` 拆文件之后，行为和公开面一个都不能变。

2026-10-15 那次拆分把 ``FeedbackService`` 的 46 个方法体搬进了
``app/domain/feedback/services/`` 这个包里的 ``visibility`` / ``reads`` /
``writes`` / ``engagement``（四个封闭词表进了 ``vocabulary``），类上只留同名转发。
调用方完全没改：``app/api/routes/feedback.py`` 和 ``admin_feedback.py`` 读的仍是
``feedback_services.FeedbackService`` / ``STATUS_LADDER`` / ``PUBLIC_TABS`` /
``ADMIN_TABS`` / ``repo.HOT_SCORE``，``platform_stats`` 与 ``shipping`` 从同一个
路径 import，``admin_feedback.feedback_services.FeedbackService`` 那种按名字打的
patch 也还在同一个对象上。靠的就是包的 ``__init__.py`` 把同一个名字空间重新摊开。

这道测试不连数据库，钉住四件事：

1. 旧路径仍然答得出原来那一组名字。
2. ``FeedbackService`` 的 47 个方法（含 ``__init__``）一个不少，而且是**真方法**
   （类自己的 ``__dict__`` 里就有），不是 ``__getattr__`` 之类动态转发的。
3. ``__init__`` 没动：``_session`` / ``_repo`` 仍从这里赋值。
4. **拆的是文件，不是行为**：每个方法体恰好是一句转发，转给四个子模块里同名、
   首参为 ``svc`` 的那一个函数。谁把方法体抄回类里一份，这条就红。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.domain.feedback import repositories
from app.domain.feedback import (
    services as feedback_services,
)
from app.domain.feedback.models import FeedbackStatus
from app.domain.feedback.services import (
    engagement,
    reads,
    visibility,
    vocabulary,
    writes,
)

# 47 个方法，照搬运之前那份 services.py 里 FeedbackService 的类体抄的。
# 顺序按定义先后；__init__ 也在内。
FEEDBACK_SERVICE_METHODS = [
    "__init__",
    "is_admin",
    "require_admin",
    "may_see",
    "visible_row",
    "may_delete_comment",
    "may_delete_feedback",
    "may_claim",
    "require_platform_room",
    "list_public",
    "list_admin",
    "list_mine",
    "counts",
    "admin_board_counts",
    "created_series",
    "reached_series",
    "count_created_between",
    "count_reached_resolved_between",
    "support_counts",
    "comment_counts",
    "supported_ids",
    "last_activity",
    "chosen_avatars",
    "thread_page",
    "replies_page",
    "comments_out",
    "detail",
    "detail_of",
    "mark_read",
    "create",
    "_create_from_proposal",
    "set_status",
    "advance",
    "_move",
    "patch_admin",
    "visible_ref",
    "claim",
    "release",
    "support",
    "unsupport",
    "comment",
    "like_comment",
    "unlike_comment",
    "_visible_comment",
    "delete_comment",
    "delete_feedback",
    "note",
]

#: 方法体搬去的那四个模块，按它们在类里被引用的名字。
SIBLINGS = {
    "visibility": visibility,
    "reads": reads,
    "writes": writes,
    "engagement": engagement,
}


def test_the_old_import_path_still_answers_its_names() -> None:
    """调用点读的就是这几个名字；少一个，那边是 ImportError 或 AttributeError。"""
    assert feedback_services.FeedbackService is not None
    assert feedback_services.ADMIN_TABS == ("public", "private", "agent", "security")
    assert feedback_services.PUBLIC_TABS == ("all", "hot", "active", "resolved")
    assert feedback_services.SORTS == ("new", "supports")
    assert feedback_services.STATUS_LADDER == (
        FeedbackStatus.received,
        FeedbackStatus.in_progress,
        FeedbackStatus.resolved,
        FeedbackStatus.deployed,
    )
    # 定义搬去了 vocabulary，名字只在那边有一份——两个路径必须是同一个对象。
    assert feedback_services.ADMIN_TABS is vocabulary.ADMIN_TABS
    assert feedback_services.PUBLIC_TABS is vocabulary.PUBLIC_TABS
    assert feedback_services.SORTS is vocabulary.SORTS
    assert feedback_services.STATUS_LADDER is vocabulary.STATUS_LADDER
    # `GET /feedback/meta` 读 `feedback_services.repo.HOT_SCORE` 这一组常量。
    assert feedback_services.repo is repositories
    assert isinstance(repositories.HOT_SCORE, (int, float))


def test_the_service_keeps_all_47_methods() -> None:
    """47 个方法一个不少，而且都是类自己定义的。"""
    assert len(FEEDBACK_SERVICE_METHODS) == 47
    found = {name for name, _ in inspect.getmembers(feedback_services.FeedbackService)}
    assert [n for n in FEEDBACK_SERVICE_METHODS if n not in found] == []

    # 真方法：类自己的 __dict__ 里就有，不是动态转发来的。
    own = set(vars(feedback_services.FeedbackService))
    assert [n for n in FEEDBACK_SERVICE_METHODS if n not in own] == []
    assert "__getattr__" not in own


def test_init_still_builds_the_session_and_the_repository() -> None:
    """拆分没动 __init__：两个属性仍在，且仍从这里赋值。"""
    source = inspect.getsource(feedback_services.FeedbackService.__init__)
    for attribute in ("_session", "_repo"):
        assert attribute in source, f"{attribute} 没在 __init__ 里赋值"


@pytest.mark.parametrize(
    "name", [n for n in FEEDBACK_SERVICE_METHODS if n != "__init__"]
)
def test_every_method_is_one_forward_to_its_sibling_module(name: str) -> None:
    """方法体恰好一句转发，转给子模块里的同名函数——实现只有一份。"""
    method = getattr(feedback_services.FeedbackService, name)
    body = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0].body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # 方法自己的 docstring 跟着方法走了
    assert len(body) == 1, f"{name} 的方法体不止一句，实现被抄回来了"

    statement = body[0]
    assert isinstance(statement, ast.Return), f"{name} 不是转发"
    call = statement.value
    if isinstance(call, ast.Await):
        call = call.value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    target_module = call.func.value
    assert isinstance(target_module, ast.Name) and target_module.id in SIBLINGS
    assert call.func.attr == name, f"{name} 转发去了同名的另一个名字 {call.func.attr}"

    # 转发把服务原样交出去：子模块函数的首参就是它。
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "self"
    forwarded = getattr(SIBLINGS[target_module.id], name)
    assert callable(forwarded)
    assert next(iter(inspect.signature(forwarded).parameters)) == "svc"
