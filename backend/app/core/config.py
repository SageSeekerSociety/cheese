"""Application configuration loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    database_url: str = "postgresql+asyncpg://cheesex:cheesex@localhost:5433/cheesex"
    db_echo: bool = False

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
    # Image the tmux backend uses (base image + tmux + ttyd + pre-accepted
    # first-launch gates). Independent of sandbox_image (the SDK path's image).
    tmux_sandbox_image: str = "cheesex-agent-tmux:latest"

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

    # --- Human auth (P1 agent-as-user / 真鉴权) ---
    # Signing secret for human session tokens (JWT HS256). Empty → derived from
    # sandbox_token when set, else a per-process random secret (fine for a single
    # worker; pin it for multi-worker / stable-across-restart deployments so a
    # redeploy doesn't invalidate every logged-in session).
    auth_token_secret: str = ""
    # Session token lifetime. Login is passwordless (handle IS the identity), so
    # this only bounds how long a minted token stays valid before re-login.
    auth_token_ttl_s: int = 7 * 24 * 3600
    # Enforce topic access for TOKEN-authenticated actors (成员/角色/项目 checks).
    # The Phase-0 handle fallback stays permissive regardless, so existing
    # (no-token) callers are unaffected. Ops kill-switch: set false to disable the
    # membership check entirely if a token rollout surfaces an unexpected block.
    authz_enforce_topic_access: bool = True

    # --- App ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
