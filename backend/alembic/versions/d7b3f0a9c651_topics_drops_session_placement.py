"""topics 丢掉 session_placement 这一列

这一列上曾经混着两件事：一间房租的那双手，和这间房里 agent 的进程跑在哪台会话机。
#1308（`c8a1d5e73f20`）把两件事拆成 `agent_sessions` 上的 `work_lease` 与
`runtime_location` 两列；#1347（`e6b4c92a07d1`）删掉 `backend/app` 里最后一个读点，
并把部署窗口里旧镜像写在房间上的那批位置认领到它自己那条会话上。

**为什么 `DROP` 排在这里，而不是跟 #1347 一起走。** app 的发布不换 device
connection owner 的镜像（`deploy/deploy-docker.sh` 逐字「leaving device connection
owner … running across this app release」），而同一次发布会跑迁移。所以 #1347 那次
发布之后，还有一个进程握着会 `SELECT` 这一列的代码：列在那时掉，它服务的每一条房间
命令——`api/routes/execution.py` 背后的工具调用、`api/routes/connector.py` 的 transcript
授权——都会从一个干净的 409/403 变成 `column topics.session_placement does not exist`，
#1240 那次三小时、1218 次失败就是这个形状。`e6b4c92a07d1` 的 docstring 写下的顺序是：
那一条先上线 → dispatch「Release device connection owner」（environment=dev，ref=main）
→ 再发这一条。前提已经满足，所以列在这里掉。

**不补跑回填。** `e6b4c92a07d1` 之后这一列零写点：新镜像全仓没有写它的地方，旧镜像的
owner 只挂 `connector` 与 `execution` 两个路由，两个都只读它。理由写在那条的 docstring
里，这里只 `DROP`。

没有 downgrade。降级要拿回的是这一列的值，而值在 #1308 与 `e6b4c92a07d1` 两次回填之后
已经在 `agent_sessions` 上，重新拼一份回来就是再造一次「一房一位置」——那正是结论 56
（按结论 60 修订）要消掉的形状。

Revision ID: d7b3f0a9c651
Revises: e5b31c07af28
Create Date: 2026-09-21

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7b3f0a9c651"
down_revision: str | Sequence[str] | None = "e5b31c07af28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("topics", "session_placement")


def downgrade() -> None:
    """没有逆，见上面那段。"""
