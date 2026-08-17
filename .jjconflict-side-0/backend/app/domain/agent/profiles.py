"""ExecutionProfile: per-project model + auth (design 2026-07-01 §2).

A profile decides which model an agent turn runs on and which provider
credentials it uses. Projects pick a profile (stored in `project.settings`);
an unset / unavailable profile falls back to the platform default ("our AI
pool"). The `claude-opus` profile is tagged `testing` — it exists for the team's
own dogfooding (a personal seat is ToS-compliant); the multi-user product path
defaults to the pool.

Compute (which node runs the sandbox) is a separate axis — see ComputeProvider.
This module only resolves model + provider env.
"""

from dataclasses import dataclass

# Profile tiers (governs availability / who may select it).
TIER_DEFAULT = "default"  # the platform pool — the safe default for everyone
TIER_TESTING = "testing"  # dogfooding only (e.g. native Claude on a personal seat)
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
    # Subscription OAuth token (from `claude setup-token`). When set, the CLI runs
    # on the subscription seat instead of an API key — see config.claude_oauth_token.
    oauth_token: str | None = None

    @property
    def available(self) -> bool:
        """A profile is selectable only if it has provider credentials — either an
        API auth token or a subscription OAuth token."""
        return bool(self.auth_token or self.oauth_token)

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
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
        }
        if self.oauth_token:
            # Subscription (走订阅): OAuth token drives auth; ANTHROPIC_AUTH_TOKEN
            # stays blanked (setting it would switch the CLI to API-key mode).
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        elif self.auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = self.auth_token
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

    def __init__(
        self,
        profiles: list[AgentProfile],
        default_name: str,
        dogfood_owners: frozenset[str] = frozenset(),
    ):
        self._profiles = {p.name: p for p in profiles}
        if default_name not in self._profiles:
            raise ValueError(f"default profile {default_name!r} not registered")
        self._default_name = default_name
        # Owners (handles) allowed to run tier=testing profiles. A testing profile
        # (e.g. native Claude on a personal seat) is only ToS-compliant for the
        # team's own dogfooding — never for a real multi-user project's members
        # (review Finding 7). Resolution hard-rejects it otherwise.
        self._dogfood_owners = dogfood_owners

    def default(self) -> AgentProfile:
        return self._profiles[self._default_name]

    def get(self, name: str | None) -> AgentProfile | None:
        return self._profiles.get(name) if name else None

    def all(self) -> list[tuple[str, AgentProfile]]:
        """Every registered profile (name, profile), default first — for the
        market catalog, which lists even the ones this owner can't select."""
        items = list(self._profiles.items())
        items.sort(key=lambda kv: (kv[0] != self._default_name, kv[0]))
        return items

    def _allowed(self, profile: AgentProfile, owner_handle: str | None) -> bool:
        if not profile.available:
            return False
        if profile.tier == TIER_TESTING:
            return owner_handle is not None and owner_handle in self._dogfood_owners
        return True

    def resolve(
        self, project_settings: dict | None, owner_handle: str | None = None
    ) -> AgentProfile:
        """Pick the project's profile, falling back to the default pool when it is
        unset, unknown, missing credentials, or not permitted for this owner
        (e.g. a testing profile on a non-dogfood project) — never a dead/illegal
        one."""
        name = (project_settings or {}).get("execution_profile")
        chosen = self.get(name)
        if chosen is None or not self._allowed(chosen, owner_handle):
            return self.default()
        return chosen

    def selectable(self, owner_handle: str | None = None) -> list[ProfileView]:
        """Profiles this owner may choose (available + permitted), default first."""
        views = [
            ProfileView(p.name, p.label, p.tier, p.model, p.available)
            for p in self._profiles.values()
            if self._allowed(p, owner_handle)
        ]
        views.sort(key=lambda v: (v.name != self._default_name, v.name))
        return views


def build_registry(settings) -> ProfileRegistry:  # type: ignore[no-untyped-def]
    """Construct the registry from app settings. The default profile is the
    configured pool (GLM today); the testing profile is native Claude, available
    only when its credentials are present."""
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
    # Anthropic-bound profiles pin their base_url explicitly: leaving it None
    # let a leaked ANTHROPIC_BASE_URL from the process env (the GLM gateway)
    # hijack the route — claude-* model names then 400 at the wrong provider.
    anthropic_url = settings.claude_base_url or "https://api.anthropic.com"
    claude = AgentProfile(
        name="claude-opus",
        label="Claude Opus（测试·仅 dogfooding）",
        tier=TIER_TESTING,
        model=settings.claude_model,
        base_url=anthropic_url,
        auth_token=settings.claude_auth_token,
        haiku_model=settings.claude_model,
        sonnet_model=settings.claude_model,
        opus_model=settings.claude_model,
        oauth_token=settings.claude_oauth_token,
    )
    # Second dogfooding channel: the Fable frontier model on the same seat —
    # lets us A/B models on real platform work by just switching the project's
    # AI pool.
    fable = AgentProfile(
        name="claude-fable",
        label="Claude Fable（测试·仅 dogfooding）",
        tier=TIER_TESTING,
        model=settings.fable_model,
        base_url=anthropic_url,
        auth_token=settings.claude_auth_token,
        haiku_model=settings.fable_model,
        sonnet_model=settings.fable_model,
        opus_model=settings.fable_model,
        oauth_token=settings.claude_oauth_token,
    )
    # The platform default is normally the pool ("default"); an operator can point
    # it at a subscription profile via settings.agent_default_profile (e.g. a demo
    # where the GLM pool is dry). Guard: fall back to "default" if the configured
    # profile is unknown or has no credentials, so boot never picks a dead pool.
    by_name = {p.name: p for p in (default, claude, fable)}
    wanted = by_name.get(settings.agent_default_profile)
    default_name = wanted.name if wanted and wanted.available else "default"
    return ProfileRegistry(
        [default, claude, fable],
        default_name=default_name,
        dogfood_owners=frozenset(settings.dogfood_owner_handles),
    )
