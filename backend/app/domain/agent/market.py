"""Resource-pool market (design v3: AI and compute are symmetric pools).

A single catalog that backs both the 市场 (browse everything on offer) and a
project's settings (pick which pool this project runs on). Two kinds of pool:

  - ``ai``      — which model/provider a turn runs on (ExecutionProfile / AIPool)
  - ``compute`` — which machine runs the sandbox (ComputeProvider / ComputePool)

`available` is the honest flag: a listing that isn't deployed/credentialed shows
in the market but can't be selected, so a project never silently runs on
something that isn't there.
"""

from dataclasses import dataclass

from app.domain.agent.profiles import ProfileRegistry

# Compute provider names (match ComputeProvider.name in compute.py).
COMPUTE_LOCAL = "local-docker"
COMPUTE_REMOTE = "remote-cheesed"
COMPUTE_GPU = "gpu"


@dataclass(frozen=True)
class PoolListing:
    kind: str  # "ai" | "compute"
    id: str
    label: str
    tier: str
    price: str  # human-readable, e.g. "包含" / "按需报价"
    description: str
    available: bool
    default: bool = False


# Curated copy for each AI profile, keyed by profile name. Falls back to a
# generic line for profiles we don't have bespoke copy for.
_AI_COPY: dict[str, tuple[str, str]] = {
    "default": (
        "包含（平台补贴）",
        "平台托管的通用大模型，开箱即用，适合绝大多数项目。",
    ),
    "claude-opus": (
        "仅团队内测",
        "原生 Claude Opus，仅团队 dogfooding 使用，不对外开放。",
    ),
}
_TIER_PRICE = {"byo": "自带凭证", "testing": "仅团队内测"}


def ai_listings(
    registry: ProfileRegistry, owner_handle: str | None = None
) -> list[PoolListing]:
    """Every AI pool in the catalog. `available` reflects credentials + whether
    this owner may select it (a testing-tier profile is unavailable off-dogfood)."""
    selectable = {v.name for v in registry.selectable(owner_handle)}
    out: list[PoolListing] = []
    for name, p in registry.all():
        price, desc = _AI_COPY.get(name, (_TIER_PRICE.get(p.tier, "—"), f"{p.label}。"))
        out.append(
            PoolListing(
                kind="ai",
                id=name,
                label=p.label,
                tier=p.tier,
                price=price,
                description=desc,
                available=name in selectable,
                default=(name == "default"),
            )
        )
    return out


def compute_listings(settings) -> list[PoolListing]:  # type: ignore[no-untyped-def]
    """Every compute pool in the catalog. Local is always on; remote is on only
    when a node is wired; GPU is a request-only market listing for now."""
    remote_ready = bool(settings.cheesed_url) and settings.compute_provider == "remote"
    return [
        PoolListing(
            kind="compute",
            id=COMPUTE_LOCAL,
            label="知是本地算力",
            tier="included",
            price="包含",
            description="平台托管的容器算力（CPU 级），适合代码、文档与数据分析。",
            available=True,
            default=True,
        ),
        PoolListing(
            kind="compute",
            id=COMPUTE_REMOTE,
            label="远程节点（自带机器）",
            tier="byo",
            price="自备 / 接入报价",
            description="把算力接到你自己的机器（cheesed 节点），数据不出你的环境。",
            available=remote_ready,
        ),
        PoolListing(
            kind="compute",
            id=COMPUTE_GPU,
            label="GPU 算力",
            tier="premium",
            price="按需报价",
            description="带 GPU 的算力，用于训练 / 推理类赛题，按小时计费，按需申请。",
            available=False,
        ),
    ]


def compute_selectable(settings) -> list[PoolListing]:  # type: ignore[no-untyped-def]
    """The compute pools a project can actually pick right now (deployed)."""
    return [p for p in compute_listings(settings) if p.available]


def compute_default_name() -> str:
    return COMPUTE_LOCAL
