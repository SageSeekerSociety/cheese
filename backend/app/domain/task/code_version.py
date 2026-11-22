"""这一版提交要评测的是哪一版代码（方案 §提交、§数据结构）。

学生点「提交」时，平台把他项目**默认分支现在指着的那个 commit** 记在这一版提交上
（``TaskSubmission.code_sha``）；之后他再推送，这一版的评测照旧跑那一个 commit。提交
页那一块「将要评测的代码」读的是同一个数（``GET /tasks/{id}/code-version``），只是多
带一句最后一次提交的说明和推送时间。

读不到就给 ``None`` —— 交作业不该因为托管方慢而交不上来：

* 项目还没有仓库（本地没配 Forgejo 的开发档）；
* 项目连的是自己的 GitHub、而 GitHub App 这一刻读不出来（安装被收回、配额用完）；
* 分支还不存在（仓库是空的，学生还没推过），或者托管方这一刻读不出来。

留空不是「没有代码」：评测时它会报「运行出错」，老师照样能手动打分（方案 §边界情况）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayUnavailableError
from app.domain.project import forge

#: 读得出默认分支的两种仓库（``ProjectForge.kind``）：平台托管的，和经平台的
#: GitHub App 连上的 GitHub 仓库（用那个安装的令牌读，``forge.branch_commit``）。
HOSTED = "forgejo"
GITHUB = "github_app"
READABLE = (HOSTED, GITHUB)


@dataclass(frozen=True)
class CodeVersion:
    """默认分支现在指着的这一版代码。"""

    sha: str
    branch: str
    #: 最后一次提交的说明（第一行）与推送时间（毫秒）。只在要展示时读
    #: （``for_project_with_commit``）；记提交时不必再问一次托管方。
    subject: str = ""
    pushed_at: int | None = None

    def to_api(self) -> dict:
        return {
            "sha": self.sha,
            "branch": self.branch,
            "subject": self.subject,
            "pushedAt": self.pushed_at,
        }


async def for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> CodeVersion | None:
    """``project_id`` 的默认分支现在指着的那个 commit；读不到时 ``None``。

    ``binding.default_branch`` 是建仓库时记下的那一个（``ProjectForge``）——不为了
    提交再问一次「默认分支叫什么」，那是一次多余的托管方调用。
    """
    try:
        binding = await forge.binding_for_project(project_id, session)
        if binding is None or binding.kind not in READABLE:
            return None
        branch = binding.default_branch
        if not branch and binding.kind == GITHUB:
            # 连上 GitHub 时没记下默认分支（老的绑定）：问一次，记在绑定上。
            branch = await forge.default_branch_if_any(project_id, session)
        branch = branch or "main"
        sha = await forge.branch_commit(project_id, session, branch)
    except GatewayUnavailableError:
        # 托管方读不出来（仓库还没建、超时、额度用完）不等于学生没交作业：这一版留空，
        # 评测那条线自有「运行出错」的说法（模块开头）。
        return None
    if not sha:
        return None
    return CodeVersion(sha=sha, branch=branch)


async def for_project_with_commit(
    session: AsyncSession, project_id: uuid.UUID
) -> CodeVersion | None:
    """``for_project`` 再加上最后一次提交的说明和推送时间。

    提交页那一块写着「默认分支当前的版本号、最后一次提交的说明和推送时间」（方案
    §提交）；说明和时间读不出来时留空，不影响版本号本身。
    """
    version = await for_project(session, project_id)
    if version is None:
        return None
    subject, pushed_at = await commit_summary(session, project_id, version.sha)
    return CodeVersion(
        sha=version.sha,
        branch=version.branch,
        subject=subject,
        pushed_at=pushed_at,
    )


async def commit_summary(
    session: AsyncSession, project_id: uuid.UUID, sha: str
) -> tuple[str, int | None]:
    """``sha`` 那一次提交的 (说明第一行, 推送时间毫秒)；读不出来时留空。

    读的是 ``/commits?sha=<sha>``——「哪一次提交、什么时候推的」，和 ``branch_head``
    读的「现在指着谁」是两件事，所以另起一次调用。读不出来不拒：这一句话是给提交页
    看的，缺了它学生照样能提交。
    """
    try:
        rows = await forge.repository_data(
            project_id,
            session,
            f"/commits?sha={quote(sha, safe='')}&per_page=1&limit=1",
        )
    except GatewayUnavailableError:
        return "", None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return "", None
    commit = rows[0].get("commit") or {}
    message = str(commit.get("message") or "")
    subject = (message.splitlines() or [""])[0]
    stamp = (commit.get("committer") or {}).get("date")
    pushed_at = None
    if stamp:
        try:
            pushed_at = int(
                datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except ValueError:
            pushed_at = None
    return subject, pushed_at
