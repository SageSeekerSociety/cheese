"""Application configuration loaded from environment / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    database_url: str = "postgresql+asyncpg://cheesex:cheesex@localhost:5433/cheesex"
    db_echo: bool = False

    # --- 主仓产品配置并入 (fusion merge I3-config): fields main's product
    # domains (avatars/materials/storage/auth) read from settings. Superset so
    # the adopted product routes boot. Defaults mirror deploy/.env.prod.example.
    # Public origin of THIS merged backend (it serves /avatars itself) — used to
    # build absolute avatar URLs in notification DTOs. Default matches the
    # scripts/dev flow (:8799); the docker dev flow and prod override it via env.
    avatar_base_url: str = "http://127.0.0.1:8799"
    storage_type: str = "local"
    storage_local_path: str = "./uploads"
    storage_local_url: str = "/uploads"
    # main's auth (real SRP/JWT login — A3): the merged app uses this as the
    # canonical identity. jwt_secret signs/verifies the product's access tokens.
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"
    frontend_url: str = "http://localhost:5200"
    # OAuth browser-flow landing pages (must match the frontend router).
    frontend_oauth_success_path: str = "/account/oauth/success"
    frontend_oauth_error_path: str = "/account/oauth/error"
    frontend_oauth_verify_path: str = "/account/oauth/verify"
    frontend_oauth_complete_path: str = "/account/oauth/complete"
    require_invite_code: bool = False
    jwt_secret: str = "dev-secret"
    access_token_expires_seconds: int = 15 * 60
    refresh_token_expires_seconds: int = 60 * 60 * 24 * 30

    # --- Agent (Claude Agent SDK) ---
    # The SDK talks to the model via the `claude` CLI. We route to a provider
    # through the Anthropic-compatible gateway (spec §9: 不绑定模型). For Zhipu
    # GLM: base_url=https://open.bigmodel.cn/api/anthropic, token=ZHIPU_API_KEY.
    # Swapping to LiteLLM / Anthropic later is just env, no code change.
    agent_model: str = "glm-5.2"
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    # Model aliases the CLI may resolve internally; map them to the provider.
    # Subagents (the Task/Agent tool) resolve via sonnet/opus → keep them on the
    # main model so 分身 don't silently run an older/weaker model.
    agent_haiku_model: str | None = "glm-4.5-air"
    agent_sonnet_model: str | None = "glm-5.2"
    agent_opus_model: str | None = "glm-5.2"
    # --- LLM gateway admin (docs/llm-gateway.md L1/L2) ---
    # When the pool routes through the self-hosted LiteLLM gateway, the backend can
    # use the gateway's ADMIN API to (L1) mint a per-project virtual key — injected
    # into the sandbox instead of the master key, so a sandbox never holds admin
    # credentials and spend is attributable per project — and read back REAL token
    # usage from /spend/logs (fixes the tmux backend's usage=0), and (L2) set a
    # per-key max_budget from the project's compute grants so the gateway refuses
    # further calls when the budget is exhausted (the mid-turn brake).
    # Unset (default) =整层关闭: env injection, usage, credits all behave as before.
    llm_gateway_admin_base: str | None = None  # e.g. http://127.0.0.1:4000
    llm_gateway_admin_key: str | None = None  # the LiteLLM master key
    # USD per compute credit — converts grant credits into a gateway max_budget.
    # Requires per-token pricing configured on the gateway models to accrue spend;
    # unset = budgets are not set (L1 metering still works, token-based).
    llm_gateway_credit_usd: float | None = None

    # ExecutionProfile "claude-opus" (tier=testing): native Claude for the team's
    # own dogfooding. Only selectable when claude_auth_token is set (an Anthropic
    # API key). base_url unset → Anthropic's default endpoint. Not the multi-user
    # product path — that defaults to the pool (see app/domain/agent/profiles.py).
    claude_model: str = "claude-opus-4-8"
    claude_base_url: str | None = None
    claude_auth_token: str | None = None
    # Subscription auth (走订阅): a long-lived OAuth token from `claude setup-token`
    # (a personal Max/Pro seat — ToS-compliant for dogfooding). Preferred over an
    # API key; when set, the sandbox `claude` runs on the subscription, not pay-
    # per-use API credits. Injected as CLAUDE_CODE_OAUTH_TOKEN.
    claude_oauth_token: str | None = None
    # ExecutionProfile "claude-fable" (tier=testing): the Fable frontier model on
    # the same personal seat/credentials as claude-opus — a second dogfooding
    # channel so we can compare models on real platform work.
    fable_model: str = "claude-fable-5"
    # Owner handles allowed to select tier=testing profiles (dogfooding only —
    # see profiles.py / review Finding 7). Comma-separated in env.
    dogfood_owner_handles: list[str] = []
    # Which registered profile is the platform default ("our AI pool"). Normally
    # "default" (the GLM pool). Set to "claude-opus"/"claude-fable" to run the
    # whole platform on the subscription seat — e.g. a demo where the GLM pool is
    # out of balance. The chosen profile must have credentials or boot fails fast.
    agent_default_profile: str = "default"
    agent_system_prompt: str = (
        "你是「芝士」，知是平台里的 AI 队友。你贯穿一个项目的全过程，"
        "了解项目的话题、决策和进展。回答要说人话，让零基础的同学也能看懂，"
        "少说废话。当你引用项目记忆里的事实时，自然地点明依据。"
    )
    # Working directory for the agent's git-backed workspace (one repo per project).
    workspace_root: str = "./.workspaces"
    # Per-turn wall-clock ceiling (review R8): a wedged turn must not hold the
    # topic lock forever. A safety net well above any real turn (coding turns run
    # minutes), not a normal-case limit — on timeout the turn is cancelled, which
    # releases the lock and tears down the in-container claude process.
    agent_turn_timeout_s: float = 900.0

    # Agent compute backend (design: two execution paths behind ComputeProvider):
    # "sdk"  → the default LocalDockerProvider: runs the Claude Agent SDK
    #          (stream-json over the cli_path shim) in a per-topic container.
    # "tmux" → TmuxHooksProvider: an interactive `claude` lives in a tmux session
    #          inside the container and is driven by tmux send-keys; structured
    #          events come back via Claude Code HTTP hooks (POST /sandbox/hooks).
    # Defaults to "sdk" so a broken tmux path never affects existing turns.
    agent_backend: str = "sdk"
    # "device" → DeviceProvider: the turn runs on a user's own enrolled machine
    #          (self-hosted / BYO compute, P3). An interactive `claude` lives in a
    #          screen the platform opens over the frozen link.Msg control channel;
    #          structured events come back via Claude Code hooks (same as tmux).
    # Image the tmux backend uses (base image + tmux + ttyd + pre-accepted
    # first-launch gates). Independent of sandbox_image (the SDK path's image).
    tmux_sandbox_image: str = "cheesex-agent-tmux:latest"

    # --- Self-hosted / BYO device compute (P3, fusion-design §5) ---
    # Public base URL a device reaches the backend at (NO /api suffix): the enrolled
    # machine's `cheese-hook` POSTs Claude Code hooks to
    # `{connector_public_base}/connector/hooks/{key}`, and the device-flow approval
    # link is built from it. For a NAT'd device this must be publicly reachable
    # (outbound-only for the link WS; the hook POST is a normal outbound request).
    connector_public_base: str = "http://localhost:8099"
    # Single-box self-hosting (fusion §5): the host path where enrolled devices see
    # this backend's `workspace_root`. When set, a device screen runs directly in the
    # topic's REAL worktree (the container worktree path translated to this host root)
    # instead of an empty scratch dir — so device edits flow through the normal
    # snapshot/accept path, no clone/sync and no out-of-band writes. Leave empty when
    # devices are remote (they own their own tree; a clone/sync path is separate).
    device_shared_workspace_host_root: str = ""
    # Optional per-install-origin override for the device's persistent control
    # channel, keyed by the origin install.sh was fetched from and mapping to a
    # plain http(s) origin that CAN carry WebSockets, e.g.
    #   {"https://cheese.ruc.edu.cn": "https://119pve.ghg.org.cn"}
    # Use it when the friendly public origin sits behind an edge that strips the
    # WebSocket Upgrade (校园前置反代 rucfd does): users still install from and
    # log in at the friendly origin — only the cheesehost control-channel WS is
    # pointed at the mapped endpoint (install.sh pre-writes the cli's "ws" key).
    # Empty (default) → behaviour unchanged. Ported from design/cheese-agent-layer
    # (CONNECTOR_WS_OVERRIDES, commits ce30e62 + 6327c7e).
    connector_ws_overrides: dict[str, str] = {}
    # Per-turn wall-clock ceiling for a device turn (mirrors agent_turn_timeout_s).
    device_turn_timeout_s: float = 900.0

    # --- MicroCloud: project machines (the team's IaaS control plane) ---
    # MicroCloud provisions the Debian machines a project gets as compute. It is
    # a separate service with its own tenants; cheese is one tenant and holds an
    # opaque secret. Empty secret = the feature reports itself unavailable, which
    # is the correct state for any deployment that isn't wired to it.
    microcloud_base_url: str = ""
    microcloud_tenant_secret: str = ""
    microcloud_timeout_s: float = 30.0
    # Pin a specific granted offering (machine type + zone + template); 0 = take
    # the first active one, which is right while a tenant is granted exactly one.
    microcloud_offering_id: int = 0
    # Requested spec. Every value is clamped into the chosen offering's own
    # range, so these are preferences, not guarantees.
    microcloud_default_cores: int = 2
    microcloud_default_memory_mb: int = 4096
    microcloud_default_disk_gb: int = 20
    microcloud_login_user: str = "cheese"
    # The project's fund account, and the balance kept in it. MicroCloud bills
    # compute against this; 0 disables top-ups (an operator funds it by hand).
    microcloud_account_name: str = "compute"
    microcloud_initial_funds: float = 1000.0
    # A ceiling per project: provisioning is one API call, and nothing else here
    # stops a loop from filling a Proxmox node.
    microcloud_max_machines_per_project: int = 2
    # How often to sweep for machines that came up and still need enrolling as
    # devices. Its own switch, NOT the project scheduler's: that one spends model
    # budget on 定期巡检 and ships off, and machines must not depend on it.
    machine_enroll_interval_seconds: int = 60

    # --- Agent sandbox (spec §9.1: 每话题在隔离容器里跑 claude + 原生工具) ---
    # When on, the interactive turn runs `claude` INSIDE a per-topic Docker
    # container (native Bash/Read/Write jailed there) via the cli_path shim, and
    # platform actions go through the `cheese` CLI → REST. Requires Docker.
    agent_sandbox_enabled: bool = False
    # Compute plane (design v3 ComputePool): "local" runs turns in a local Docker
    # sandbox; "remote" ships each turn to a cheesed node at cheesed_url. The node's
    # container calls cheese back to cheesed_cheese_api (the backend's address that's
    # reachable FROM the node — host.docker.internal works when the node is local).
    compute_provider: str = "local"
    cheesed_url: str = "http://localhost:8100"
    cheesed_cheese_api: str = "http://host.docker.internal:8099/api"
    sandbox_image: str = "cheesex-agent-sandbox:latest"
    sandbox_shim: str = "./sandbox/claude-sbx"
    # Base URL the in-container `cheese` CLI calls back to (host → backend).
    sandbox_api_base: str = "http://host.docker.internal:8099/api"
    # Shared secret the sandbox `cheese` CLI sends (X-Cheese-Token) so the
    # cheese write-API isn't open on the bind address. Empty → generated per
    # process (fine for a single worker; pin it for multi-worker deployments).
    sandbox_token: str = ""

    def agent_env(self) -> dict[str, str]:
        """Env vars passed to the SDK/CLI to select the model provider."""
        env: dict[str, str] = {}
        if self.anthropic_base_url:
            env["ANTHROPIC_BASE_URL"] = self.anthropic_base_url
        if self.anthropic_auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = self.anthropic_auth_token
        if self.agent_haiku_model:
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = self.agent_haiku_model
        if self.agent_sonnet_model:
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self.agent_sonnet_model
        if self.agent_opus_model:
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.agent_opus_model
        return env

    # --- Compute credits (spec §9.1 机构提供算力 → real quotas) ---
    # Conversion rate: how many tokens one compute credit buys. A turn's token
    # usage is folded into credits and deducted from the project's grants
    # (oldest grant first). Default: 1 credit = 10k tokens.
    compute_credit_tokens: int = 10_000
    # Project-level concurrency ceiling: at most this many agent turns run at
    # once per project; turns beyond it queue (visible as a system event).
    # Overridable per project via project.settings["max_concurrent_turns"].
    max_concurrent_turns: int = 2

    # --- Scheduler (spec §9.1: 确定性调度——定时巡检/生命周期) ---
    # Seconds between automatic 定期巡检 ticks across all projects. 0 = off
    # (manual heartbeat only; default off so dev/tests don't burn model calls).
    scheduler_interval_seconds: int = 0

    # --- Memory backend (spec §8.4 / §15 Q9) ---
    # "db": flat memory_entries projection in PG (Phase 0 default, no extra deps).
    # "openviking": real layered memory on embedded OpenViking (viking:// FS,
    # L0/L1/L2 levels, semantic search, LLM extraction). Fully local storage;
    # needs an OpenAI-compatible chat + embedding endpoint for extraction/vectors.
    memory_backend: str = "db"
    # Local storage root for the embedded OpenViking instance (AGFS + vectors).
    openviking_data_dir: str = "./.viking"
    # OpenAI-compatible endpoints OpenViking uses internally. These are separate
    # from anthropic_base_url (the agent gateway speaks the Anthropic protocol;
    # OpenViking needs the OpenAI protocol). For Zhipu the same API key works on
    # both gateways. api keys default to anthropic_auth_token when unset.
    openviking_llm_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    openviking_llm_model: str = "glm-4.5-air"
    openviking_llm_api_key: str | None = None
    openviking_embedding_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    openviking_embedding_model: str = "embedding-3"
    openviking_embedding_api_key: str | None = None
    openviking_embedding_dimension: int = 2048
    # 知识沉淀是副产品 (spec §8.4): commit each finished turn to OpenViking so
    # memories are extracted in the background. Only effective on "openviking".
    openviking_auto_extract: bool = True
    # Memory types OpenViking's extractor may write (built-in taxonomy names).
    # Curated to the omem-style durable kinds — omem:user→profile/preferences,
    # omem:feedback→preferences, omem:project→events, omem:reference→entities.
    # identity/soul are the extractor's anchor files and MUST stay allowed
    # (verified: without them the extraction loop writes nothing at all).
    # trajectories/experiences are agent-SOP records that bloat recall: off.
    openviking_memory_types: list[str] = [
        "profile",
        "preferences",
        "entities",
        "events",
        "tools",
        "identity",
        "soul",
    ]

    # --- OAuth login providers (read via getattr in app.domain.oauth.services;
    # they MUST be declared here — Settings has extra="ignore", so undeclared
    # env vars are silently dropped and the feature can never be configured) ---
    oauth_enabled_providers: str = ""  # comma-separated: "github,google,ruc"
    oauth_github_client_id: str | None = None
    oauth_github_client_secret: str | None = None
    oauth_github_redirect_url: str | None = None
    oauth_google_client_id: str | None = None
    oauth_google_client_secret: str | None = None
    oauth_google_redirect_url: str | None = None
    oauth_ruc_client_id: str | None = None
    oauth_ruc_client_secret: str | None = None
    oauth_ruc_redirect_url: str | None = None

    # --- WebAuthn / passkeys (same declare-or-dropped rule as above) ---
    # webauthn_origin MUST exactly match the scheme://host:port in the browser
    # address bar or registration fails ("Unexpected client data origin"). rp_id
    # is the registrable domain only (no port), so localhost covers every local
    # port. Default matches the vite prod port (3000, = `task dev` and e2e);
    # override WEBAUTHN_ORIGIN for other ports (the demo runs vite on :5200).
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Cheese Community"
    webauthn_origin: str = "http://localhost:3000"

    # --- S3 storage (used when storage_type == "s3") ---
    s3_bucket: str = "cheese"
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    s3_public_url: str | None = None

    # --- Human auth (P1 agent-as-user / 真鉴权) ---
    # Session tokens (JWT HS256) are signed AND verified with the one
    # ``jwt_secret`` (same secret as main's access/refresh tokens) — no
    # per-module signing secret.
    # Session token lifetime. Login is passwordless (handle IS the identity), so
    # this only bounds how long a minted token stays valid before re-login.
    auth_token_ttl_s: int = 7 * 24 * 3600
    # Enforce topic access for TOKEN-authenticated actors (成员/角色/项目 checks).
    # The Phase-0 handle fallback stays permissive regardless, so existing
    # (no-token) callers are unaffected. Ops kill-switch: set false to disable the
    # membership check entirely if a token rollout surfaces an unexpected block.
    authz_enforce_topic_access: bool = True

    # --- 主仓产品配置并入 (fusion merge, restored): main's live product domains
    # (task AI advice, rank checks, email/notifications, meilisearch, real-name
    # encryption) read these off settings. The merge dropped them, so those code
    # paths hit AttributeError at runtime; restored verbatim from origin/main
    # (aliases kept where the env var name differs from the field name). ---
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    openai_default_model: str = Field(
        default="gpt-4o-mini", alias="OPENAI_DEFAULT_MODEL"
    )
    openai_reasoning_model: str = Field(
        default="o1-mini", alias="OPENAI_REASONING_MODEL"
    )
    openai_temperature: float = Field(default=0.7, alias="OPENAI_TEMPERATURE")
    openai_max_tokens: int = Field(default=4096, alias="OPENAI_MAX_TOKENS")
    openai_timeout_seconds: float = Field(default=180.0, alias="OPENAI_TIMEOUT_SECONDS")
    openai_pdf_timeout_seconds: float = Field(
        default=300.0, alias="OPENAI_PDF_TIMEOUT_SECONDS"
    )
    ai_daily_quota: float = Field(default=10.0, alias="AI_DAILY_QUOTA")

    email_from_address: str = Field(default="", alias="EMAIL_FROM_ADDRESS")
    email_smtp_host: str = Field(default="", alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=587, alias="EMAIL_SMTP_PORT")
    email_smtp_username: str = Field(default="", alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    email_smtp_ssl: bool = Field(default=False, alias="EMAIL_SMTP_SSL_ENABLE")

    notification_dedup_ttl_seconds: int = Field(
        default=10 * 60, alias="NOTIFICATION_DEDUP_TTL_SECONDS"
    )
    notification_email_batch_size: int = Field(
        default=100, alias="NOTIFICATION_EMAIL_BATCH_SIZE"
    )
    notification_email_queue_key: str = Field(
        default="cheese:notifications:email", alias="NOTIFICATION_EMAIL_QUEUE_KEY"
    )
    notification_email_max_retries: int = Field(
        default=3, alias="NOTIFICATION_EMAIL_MAX_RETRIES"
    )

    meilisearch_url: str = Field(default="", alias="MEILISEARCH_URL")
    meilisearch_api_key: str = Field(default="", alias="MEILISEARCH_API_KEY")

    enforce_task_participant_limit_check: bool = Field(
        default=False, alias="APPLICATION_ENFORCE_TASK_PARTICIPANT_LIMIT_CHECK"
    )
    rank_check_enforced: bool = Field(
        default=False, alias="APPLICATION_RANK_CHECK_ENFORCED"
    )
    rank_jump: int = Field(default=1, alias="APPLICATION_RANK_JUMP")
    realname_encryption_key: str = Field(default="", alias="REALNAME_ENCRYPTION_KEY")

    # --- App ---
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5200",
        "http://localhost:5200",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
