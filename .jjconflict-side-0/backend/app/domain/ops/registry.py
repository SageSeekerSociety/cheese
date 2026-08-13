"""Operation registry — the authority behind the operation card's 七问.

Why a registry at all: the card's seven questions (要执行什么 / 打到哪 / 可逆吗 /
炸了会怎样 / 能中断吗 …) must NOT be free text an agent fills in. Free text is
exactly the field an over-eager author writes "低风险，随时可回滚" into. So the
manifest carries only `operation_id` + `args`, and every other answer is
*derived* here — `manifest.render_resolved` recomputes them and the validator
rejects a manifest whose committed answers disagree with the registry.

拍板 6 (2026-08-11) lands here too: `blast_radius` is what decides whether a
request may ride a long-lived authorization envelope. Only `none` may; anything
with side effects is one-shot, one approve. See `BlastRadius`.

NOTHING in this module executes anything — it only describes. The workflow names
below are recorded so a later step (and a human reading the PR) knows what WOULD
run; dispatching them is out of scope by design.
"""

import enum
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

# ---------------------------------------------------------------------------
# Pinned argument types — see manifest.py for the companion rule that bans
# implicit "current"/"latest"/"main" values anywhere in args. These types are the
# first line of that defence: a branch name simply cannot satisfy them.
# ---------------------------------------------------------------------------

PinnedSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
"""A full 40-hex commit sha. `main` moves; a sha does not."""

PinnedVersion = Annotated[
    str, StringConstraints(pattern=r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
]
"""A release tag like `v0.16.4`. Not `latest`, not `current`."""

Handle = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


class BlastRadius(enum.StrEnum):
    """How far the damage reaches if this operation goes wrong.

    This is the landing point of 拍板 6：**每次执行都要一次新的 approve**. A
    long-lived envelope PR may only carry `none` — operations whose blast radius
    is contained by the envelope itself. Anything else is a one-shot PR that
    closes after it runs. The envelope saves the cost of *explaining*, never the
    cost of a human clicking.
    """

    none = "none"
    """No side effect outside the run itself (read-only probes, dry runs)."""

    dev = "dev"
    """Touches the dev/test box. Recoverable by redeploying."""

    prod = "prod"
    """Touches production — users see it."""

    @property
    def envelope_eligible(self) -> bool:
        return self is BlastRadius.none


class Reversibility(enum.StrEnum):
    reversible = "reversible"
    partial = "partial"
    irreversible = "irreversible"


class Resolved(BaseModel):
    """The derived answers shown on the card face. Never author-written."""

    model_config = ConfigDict(extra="forbid")

    what: str
    """要执行什么 — one concrete sentence, with the pinned args inlined."""

    where: str
    """打到哪 — the actual target (box, repo, environment)."""

    blast_radius: BlastRadius
    reversibility: Reversibility

    reversal: str
    """可逆吗 — how it is undone, or why it cannot be."""

    worst_case: str
    """炸了会怎样 — the realistic bad outcome, not the average one."""

    interruptible: bool
    """能中断吗 — whether killing it mid-flight is safe."""

    human_approvals_required: int
    """How many human authorizations execution will need (declared now,
    enforced when the execution link is built — step 4/5, not here)."""


class OperationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Operation(ABC):
    """Type-erased facade so the registry can hold heterogeneous operations."""

    operation_id: str
    title: str
    workflow: str
    """The GitHub workflow that WOULD run it. Recorded, never dispatched here."""

    @abstractmethod
    def resolve_args(self, raw: Mapping[str, object]) -> OperationArgs:
        """Parse+validate raw manifest args. Raises pydantic ValidationError."""

    @abstractmethod
    def resolve(self, raw: Mapping[str, object]) -> Resolved:
        """Derive the card face from validated args."""

    @abstractmethod
    def args_schema(self) -> dict[str, object]:
        """JSON schema for this operation's args (for tooling + docs)."""


class TypedOperation[ArgsT: OperationArgs](Operation):
    """Base for a concrete operation: declares its args model once, and gets
    parsing/erasure for free so subclasses only write `describe(args)`."""

    args_model: type[ArgsT]

    def resolve_args(self, raw: Mapping[str, object]) -> ArgsT:
        return self.args_model.model_validate(dict(raw))

    def resolve(self, raw: Mapping[str, object]) -> Resolved:
        return self.describe(self.resolve_args(raw))

    def args_schema(self) -> dict[str, object]:
        return self.args_model.model_json_schema()

    @abstractmethod
    def describe(self, args: ArgsT) -> Resolved: ...


# ---------------------------------------------------------------------------
# The operations themselves. Small on purpose: every entry here is a real
# workflow in .github/workflows, and each one a human has to be able to judge
# from the card alone.
# ---------------------------------------------------------------------------


class DeviceSmokeArgs(OperationArgs):
    commit_sha: PinnedSha
    """Which commit of this repo the smoke run is checked out at."""

    project_id: str
    user_handle: Handle
    accept: bool = False
    """The workflow's own `accept` input: when true the resulting card is
    accepted, which LANDS work on the upstream trunk. That single flag is the
    difference between a read-only probe and a write — so it moves the blast
    radius, and with it the right to ride an envelope."""


class DeviceSmoke(TypedOperation[DeviceSmokeArgs]):
    operation_id = "device.smoke"
    title = "设备自建冒烟（device-smoke）"
    workflow = ".github/workflows/device-smoke.yml"
    args_model = DeviceSmokeArgs

    def describe(self, args: DeviceSmokeArgs) -> Resolved:
        landing = args.accept
        return Resolved(
            what=(
                f"在 dev box 上以 {args.commit_sha[:12]} 跑一轮真实 device-backed "
                f"turn（项目 {args.project_id}，以 {args.user_handle} 的身份）"
                + ("，并采纳产生的验收卡" if landing else "，不采纳产生的验收卡")
            ),
            where="dev box（self-hosted runner: cheese-dev）",
            blast_radius=BlastRadius.dev if landing else BlastRadius.none,
            reversibility=(
                Reversibility.partial if landing else Reversibility.reversible
            ),
            reversal=(
                "采纳已把这一轮的产出合进上游 trunk，只能再提一个 revert 提交回退"
                if landing
                else "只产生一个话题和一轮对话，删掉话题即可；不写任何上游分支"
            ),
            worst_case=(
                "一轮未经审阅的 agent 产出被合进上游 trunk"
                if landing
                else "白花一轮模型预算；dev box 的 runner 被占用最多 25 分钟"
            ),
            interruptible=True,
            human_approvals_required=1,
        )


class DeployDevArgs(OperationArgs):
    commit_sha: PinnedSha
    """The exact commit whose per-commit images get deployed."""


class DeployDev(TypedOperation[DeployDevArgs]):
    operation_id = "deploy.dev"
    title = "部署 dev box"
    workflow = ".github/workflows/deploy-dev.yml"
    args_model = DeployDevArgs

    def describe(self, args: DeployDevArgs) -> Resolved:
        return Resolved(
            what=(
                f"以 {args.commit_sha[:12]} 的 per-commit 镜像跑 "
                "deploy/deploy-docker.sh（pull → migrate → up → health-check）"
            ),
            where="dev/test box cheese-dev-env1-app（192.168.16.5）",
            blast_radius=BlastRadius.dev,
            reversibility=Reversibility.partial,
            reversal=(
                "重新部署上一个 commit 的镜像即可回退代码；"
                "但这一次跑过的 alembic 迁移不会自动回滚"
            ),
            worst_case=(
                "dev 环境在迁移或健康检查失败后停在半升级状态，"
                "所有人的 dev 联调中断，直到有人手工修迁移"
            ),
            interruptible=False,
            human_approvals_required=1,
        )


class DeployProdArgs(OperationArgs):
    release_tag: PinnedVersion
    """A published release tag. prod deploys releases, not main commits."""


class DeployProd(TypedOperation[DeployProdArgs]):
    operation_id = "deploy.prod"
    title = "部署 prod box"
    workflow = ".github/workflows/deploy-prod.yml"
    args_model = DeployProdArgs

    def describe(self, args: DeployProdArgs) -> Resolved:
        return Resolved(
            what=f"把 release {args.release_tag} 部署到线上（deploy/deploy-docker.sh）",
            where="prod box cheese-prod-app（192.168.16.8 → cheese.ruc.edu.cn）",
            blast_radius=BlastRadius.prod,
            reversibility=Reversibility.partial,
            reversal=(
                "回退到上一个 release 可以恢复代码；数据库迁移一旦跑过就不可逆，"
                "只能靠备份恢复"
            ),
            worst_case="线上不可用；不可逆迁移损坏数据，只能从备份恢复并丢失期间的写入",
            interruptible=False,
            human_approvals_required=2,
        )


class BackupRestoreTestArgs(OperationArgs):
    commit_sha: PinnedSha
    """Which commit's restore scripts are exercised."""


class BackupRestoreTest(TypedOperation[BackupRestoreTestArgs]):
    operation_id = "backup.restore_test"
    title = "备份恢复演练"
    workflow = ".github/workflows/backup-restore-test.yml"
    args_model = BackupRestoreTestArgs

    def describe(self, args: BackupRestoreTestArgs) -> Resolved:
        return Resolved(
            what=(
                f"以 {args.commit_sha[:12]} 的脚本把最近一份备份恢复进一次性环境并校验"
            ),
            where="一次性的恢复环境（不碰 dev / prod 的任何库）",
            blast_radius=BlastRadius.none,
            reversibility=Reversibility.reversible,
            reversal="演练环境用完即弃，无需撤销",
            worst_case="演练失败，说明备份不可用——这正是要提前知道的事",
            interruptible=True,
            human_approvals_required=1,
        )


_OPERATIONS: tuple[Operation, ...] = (
    DeviceSmoke(),
    DeployDev(),
    DeployProd(),
    BackupRestoreTest(),
)

REGISTRY: Mapping[str, Operation] = {op.operation_id: op for op in _OPERATIONS}


class UnknownOperationError(LookupError):
    def __init__(self, operation_id: str) -> None:
        known = "、".join(sorted(REGISTRY))
        super().__init__(
            f"未知 operation_id {operation_id!r}；registry 里只有：{known}"
        )
        self.operation_id = operation_id


def get_operation(operation_id: str) -> Operation:
    try:
        return REGISTRY[operation_id]
    except KeyError:
        raise UnknownOperationError(operation_id) from None


def describe_registry() -> list[dict[str, object]]:
    """Registry as plain data — for `GET /api/ops/registry`, docs, and agents
    that need to know what they are allowed to ask for."""
    return [
        {
            "operation_id": op.operation_id,
            "title": op.title,
            "workflow": op.workflow,
            "args_schema": op.args_schema(),
        }
        for op in _OPERATIONS
    ]


__all__ = [
    "REGISTRY",
    "BlastRadius",
    "Handle",
    "Operation",
    "OperationArgs",
    "PinnedSha",
    "PinnedVersion",
    "Resolved",
    "Reversibility",
    "UnknownOperationError",
    "ValidationError",
    "describe_registry",
    "get_operation",
]
