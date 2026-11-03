"""ExecutionProfile: per-project model + auth (design 2026-07-01 §2).

A profile decides which model an agent turn runs on and which provider
credentials it uses. Projects pick a profile (stored in `project.settings`);
an unset / unknown / unavailable profile falls back to the platform default
("our AI pool").

Compute (which node runs the sandbox) is a separate axis — see ComputePool.
This module only resolves model + provider env.
"""

from dataclasses import dataclass

# Profile tiers (governs availability / who may select it).
TIER_DEFAULT = "default"  # the platform pool — the safe default for everyone
TIER_BYO = "byo"  # project brings its own credentials


@dataclass(frozen=True)
class AgentProfile:
    name: str
    label: str
    tier: str
    model: str
    base_url: str | None
    auth_token: str | None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None

    @property
    def available(self) -> bool:
        """A profile is selectable only if it has provider credentials."""
        return bool(self.auth_token)

    def full_env(self) -> dict[str, str]:
        """Provider env for the `claude` CLI/SDK. Replaces (not merges) the
        default provider env so a Claude profile never inherits the GLM gateway's
        base_url or model aliases."""
        # Every key is set EXPLICITLY — unused ones to "" — because the CLI
        # subprocess inherits the backend's os.environ, and a leaked
        # ANTHROPIC_AUTH_TOKEN/BASE_URL from another provider silently hijacks
        # routing or auth (Zhipu bearer at api.anthropic.com → 401; GLM URL for
        # a claude-* model → 400). Empty string reads as unset to the CLI but
        # MASKS any inherited value.
        env: dict[str, str] = {
            "ANTHROPIC_BASE_URL": self.base_url or "",
            "ANTHROPIC_AUTH_TOKEN": self.auth_token or "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
        }
        # Map ALL alias tiers: a newer claude CLI reaches for sonnet/opus
        # aliases (subagent defaults etc.) — an unmapped alias hits the GLM
        # gateway as a claude-* name and 400s the whole turn ([1211]).
        if self.haiku_model:
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = self.haiku_model
        if self.sonnet_model:
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self.sonnet_model
        if self.opus_model:
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.opus_model
        # Subagents resolve via the sonnet/opus aliases — pin them to this
        # profile's model so 分身 never silently run a different provider.
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self.model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.model
        return env


@dataclass(frozen=True)
class ProfileView:
    """Safe, credential-free view of a profile (for API listings)."""

    name: str
    label: str
    tier: str
    model: str
    available: bool


class ProfileRegistry:
    """The set of execution profiles + per-project resolution."""

    def __init__(self, profiles: list[AgentProfile], default_name: str):
        self._profiles = {p.name: p for p in profiles}
        if default_name not in self._profiles:
            raise ValueError(f"default profile {default_name!r} not registered")
        self._default_name = default_name

    def default(self) -> AgentProfile:
        return self._profiles[self._default_name]

    def get(self, name: str | None) -> AgentProfile | None:
        return self._profiles.get(name) if name else None

    def resolve(self, project_settings: dict | None) -> AgentProfile:
        """Pick the project's profile, falling back to the default pool when it is
        unset, unknown (a profile since removed, such as the old `claude-opus`
        and `claude-fable`), or missing credentials — never a dead one."""
        name = (project_settings or {}).get("execution_profile")
        chosen = self.get(name)
        if chosen is None or not chosen.available:
            return self.default()
        return chosen

    def selectable(self) -> list[ProfileView]:
        """Profiles a project may choose (available), default first."""
        views = [
            ProfileView(p.name, p.label, p.tier, p.model, p.available)
            for p in self._profiles.values()
            if p.available
        ]
        views.sort(key=lambda v: (v.name != self._default_name, v.name))
        return views


def build_registry(settings) -> ProfileRegistry:  # type: ignore[no-untyped-def]
    """Construct the registry from app settings: the configured pool, reached
    through the gateway."""
    default = AgentProfile(
        name="default",
        label="知是 AI Pool（默认）",
        tier=TIER_DEFAULT,
        model=settings.agent_model,
        base_url=settings.anthropic_base_url,
        auth_token=settings.anthropic_auth_token,
        haiku_model=settings.agent_haiku_model,
        sonnet_model=settings.agent_sonnet_model,
        opus_model=settings.agent_opus_model,
    )
    return ProfileRegistry([default], default_name="default")
