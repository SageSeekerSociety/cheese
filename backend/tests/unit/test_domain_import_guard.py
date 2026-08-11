"""架构守卫：领域包不许直接 import 别的领域的 repository 模块。

正路是 service → 对方的 service。直接摸对方的 repository 会把「数据访问」这层的
约束（谁能读、读完要不要补别的字段、要不要发通知）绕过去。本领域内部随便 import，
不受限。

**这道守卫治的是分层纪律，不是依赖环——别指望它把环拆没。** 按正路改完，包和包之间
那条边还在，只是从 repository 层挪到了 services 层；包级依赖图一条边都不会少。真要解
环得消除依赖本身（倒置控制、把数据传进来、把逻辑搬家），那是另一件事，成本高得多。

实测（2026-08-11，42 个领域包 / 224 条跨包 import）：``models`` 77 条、
``repositories`` 77 条、``services`` 16 条且无环。把跨域 repository 边整个**删掉**
（不是改道），双向依赖对 20 → 9、强连通分量 26 个包 → 18 个；把 ``models`` 边也删掉
才降到两个 3 包的小环。也就是说 ORM 关系那半边贡献的环一样多，而那半边是设计上难免
的。所以：**不要拿本守卫的通过率当解环进度。**

## 白名单

存量违规全部写死在下面的 ``_EXEMPT`` 里，**故意不放配置文件**：加一条豁免必须改
代码、走 review，而不是偷偷加一行配置。

白名单是**棘轮**——多一条会红（新违规），少一条也会红（还完债忘了删）。所以还完
一对依赖，必须把对应的行删掉，红了就是提醒你删。

粒度是 (发起方模块, 被摸的 repository 模块)，不到类名。理由：同一个文件从同一个
领域的 repository 里多拿一个类，架构上还是同一处违规，不值得再走一次 review。
"""

import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "app" / "domain"

# ---------------------------------------------------------------------------
# 存量豁免。格式：(发起方模块, 被 import 的 repository 模块)
#
# 还债进度见 docs/topics/领域包解环.md。删行的正确姿势：把 service 改成调对方的
# service，然后删掉这里对应的行，跑本测试确认不红。
# ---------------------------------------------------------------------------
_EXEMPT: frozenset[tuple[str, str]] = frozenset(
    {
        # --- agent ---
        ("app.domain.agent.roles", "app.domain.expert_role.repositories"),
        ("app.domain.agent.chat", "app.domain.block.repositories"),
        ("app.domain.agent.chat", "app.domain.milestone.repositories"),
        ("app.domain.agent.chat", "app.domain.project.repositories"),
        ("app.domain.agent.chat", "app.domain.review.repositories"),
        ("app.domain.agent.chat", "app.domain.team.repositories"),
        ("app.domain.agent.chat", "app.domain.topic.repositories"),
        ("app.domain.agent.chat", "app.domain.usage.repositories"),
        ("app.domain.agent.device_provider", "app.domain.machine.repositories"),
        ("app.domain.agent.device_provider", "app.domain.user.repositories"),
        ("app.domain.agent.github_app", "app.domain.project.repositories"),
        ("app.domain.agent.sandbox_notices", "app.domain.block.repositories"),
        ("app.domain.agent.sandbox_notices", "app.domain.topic.repositories"),
        # --- answers / comments / discussion / groups ---
        ("app.domain.answers.services", "app.domain.user.repositories"),
        ("app.domain.answers.services", "app.domain.questions.repositories"),
        ("app.domain.comments.services", "app.domain.user.repositories"),
        ("app.domain.discussion.services", "app.domain.user.repositories"),
        ("app.domain.groups.services", "app.domain.user.repositories"),
        # --- cx_notification / cx_task ---
        ("app.domain.cx_notification.services", "app.domain.block.repositories"),
        ("app.domain.cx_notification.services", "app.domain.project.repositories"),
        ("app.domain.cx_task.services", "app.domain.space.repositories"),
        # --- dashboard（读模型，横跨 7 个领域聚合，单独还） ---
        ("app.domain.dashboard.services", "app.domain.cx_notification.repositories"),
        ("app.domain.dashboard.services", "app.domain.cx_task.repositories"),
        ("app.domain.dashboard.services", "app.domain.membership.repositories"),
        ("app.domain.dashboard.services", "app.domain.milestone.repositories"),
        ("app.domain.dashboard.services", "app.domain.project.repositories"),
        ("app.domain.dashboard.services", "app.domain.space.repositories"),
        ("app.domain.dashboard.services", "app.domain.topic.repositories"),
        ("app.domain.dashboard.services", "app.domain.user.repositories"),
        # --- identity / knowledge / machine / membership / milestone / oauth ---
        ("app.domain.identity.services", "app.domain.user.repositories"),
        ("app.domain.knowledge.services", "app.domain.team.repositories"),
        ("app.domain.knowledge.services", "app.domain.user.repositories"),
        ("app.domain.machine.services", "app.domain.project.repositories"),
        ("app.domain.membership.services", "app.domain.project.repositories"),
        ("app.domain.milestone.services", "app.domain.project.repositories"),
        ("app.domain.oauth.services", "app.domain.user.repositories"),
        # --- project ---
        ("app.domain.project.services", "app.domain.topic.repositories"),
        ("app.domain.project.services", "app.domain.cx_task.repositories"),
        ("app.domain.project.services", "app.domain.usage.repositories"),
        ("app.domain.project.services", "app.domain.user.repositories"),
        # --- questions ---
        ("app.domain.questions.services", "app.domain.user.repositories"),
        ("app.domain.questions.services", "app.domain.answers.repositories"),
        # --- review / scheduler ---
        ("app.domain.review.pr_publish", "app.domain.topic.repositories"),
        ("app.domain.review.services", "app.domain.cx_task.repositories"),
        ("app.domain.review.services", "app.domain.membership.repositories"),
        ("app.domain.review.services", "app.domain.project.repositories"),
        ("app.domain.review.services", "app.domain.topic.repositories"),
        ("app.domain.scheduler.service", "app.domain.project.repositories"),
        # --- space ---
        ("app.domain.space.analytics_service", "app.domain.task.repositories"),
        ("app.domain.space.analytics_service", "app.domain.user.repositories"),
        ("app.domain.space.analytics_view_service", "app.domain.user.repositories"),
        (
            "app.domain.space.member_participating_service",
            "app.domain.user.repositories",
        ),
        ("app.domain.space.services", "app.domain.task.repositories"),
        # --- task ---
        ("app.domain.task.services", "app.domain.space.repositories"),
        ("app.domain.task.services", "app.domain.team.repositories"),
        ("app.domain.task.services", "app.domain.user.repositories"),
        ("app.domain.task.visibility_service", "app.domain.space.repositories"),
        ("app.domain.task.visibility_service", "app.domain.user.repositories"),
        # --- team / team_project ---
        ("app.domain.team.services", "app.domain.task.repositories"),
        ("app.domain.team_project.services", "app.domain.team.repositories"),
        ("app.domain.team_project.services", "app.domain.user.repositories"),
        # --- topic / topic_membership ---
        ("app.domain.topic.services", "app.domain.block.repositories"),
        ("app.domain.topic.services", "app.domain.project.repositories"),
        ("app.domain.topic_membership.services", "app.domain.identity.repositories"),
        ("app.domain.topic_membership.services", "app.domain.topic.repositories"),
        ("app.domain.topic_membership.services", "app.domain.user.repositories"),
        # --- webhook / workspace ---
        ("app.domain.usage.subscription_ingest", "app.domain.project.repositories"),
        ("app.domain.webhook.service", "app.domain.block.repositories"),
        ("app.domain.workspace.dogfood_notices", "app.domain.block.repositories"),
        ("app.domain.workspace.dogfood_notices", "app.domain.topic.repositories"),
    }
)


def _is_repository_module(name: str) -> bool:
    """模块名的最后一段看起来是不是 repository 层。

    覆盖 ``repositories`` / ``repository`` / ``sql_repository`` /
    ``recruitment_repositories`` 这几种既有写法——不然改个名就能绕过去。
    """
    last = name.rsplit(".", 1)[-1]
    if last in ("repository", "repositories"):
        return True
    return last.endswith(("_repository", "_repositories"))


def _domain_of(module: str) -> str | None:
    """``app.domain.<pkg>.<...>`` → ``<pkg>``；不在领域包下则 None。"""
    parts = module.split(".")
    if len(parts) < 4 or parts[0] != "app" or parts[1] != "domain":
        return None
    return parts[2]


def _module_name(path: Path) -> str:
    rel = path.relative_to(DOMAIN_ROOT.parents[1])  # backend/app 的上一级
    return ".".join(rel.with_suffix("").parts)


def _resolve(node: ast.ImportFrom, current: str) -> str | None:
    """把相对 import 还原成绝对模块路径。"""
    if node.level == 0:
        return node.module
    base = current.split(".")[: -node.level]
    return ".".join(base + (node.module.split(".") if node.module else []))


def _scan() -> list[tuple[str, str, int]]:
    """返回全部跨领域 repository import：(发起方模块, 目标模块, 行号)。"""
    found: list[tuple[str, str, int]] = []
    for py in sorted(DOMAIN_ROOT.rglob("*.py")):
        src_mod = _module_name(py)
        src_dom = _domain_of(src_mod)
        if src_dom is None:
            continue  # domain/common.py 这类顶层文件，不属于任何领域包
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = _resolve(node, src_mod)
                if not mod:
                    continue
                # 两种写法都要抓：
                #   from app.domain.x.repositories import Foo
                #   from app.domain.x import repositories
                targets = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for t in targets:
                if not _is_repository_module(t):
                    continue
                dst_dom = _domain_of(t)
                if dst_dom is None or dst_dom == src_dom:
                    continue
                found.append((src_mod, t, node.lineno))
    return found


def test_no_cross_domain_repository_imports() -> None:
    """跨领域 import 别人的 repository：白名单之外一律不许。"""
    violations = sorted({(src, dst) for src, dst, _ in _scan()} - _EXEMPT)
    if violations:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in violations)
        pytest.fail(
            "发现跨领域 repository import：\n"
            f"{lines}\n\n"
            "正路是让本领域的 service 调对方的 service，"
            "而不是直接摸对方的 repository。\n"
            "确实拆不动的，把上面的行加进本文件的 _EXEMPT，并在 PR 里说明为什么。"
        )


def test_exemptions_are_all_still_needed() -> None:
    """白名单是棘轮：还完债必须把行删掉，不许留着虚胖。"""
    actual = {(src, dst) for src, dst, _ in _scan()}
    stale = sorted(_EXEMPT - actual)
    if stale:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in stale)
        pytest.fail(
            "以下豁免已经没有对应的违规了，请从本文件的 _EXEMPT 里删掉：\n"
            f"{lines}\n\n"
            "（债还完了忘删白名单，下一个人就会以为这条依赖还在。）"
        )
