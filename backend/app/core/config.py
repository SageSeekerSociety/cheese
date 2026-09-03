"""Application configuration loaded from environment / .env."""

import hashlib
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The gateway mounts this whole app under `/api` and strips that one segment
# (`proxy_pass http://backend:8081/`), so a backend route is bare while the URL
# the browser used is `/api` + that route. Anything the BROWSER will resolve
# against — a cookie's Path, a rewritten asset URL, the base a dev server has to
# be started under — has to be built with this. It lives in core rather than in
# the API layer because the device launcher needs the same string to tell an
# agent where its app will be mounted, and the domain cannot import the API.
GATEWAY_MOUNT = "/api"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    # The dev database the repo-root docker-compose.yml publishes on :5432. The
    # test server is a SEPARATE container on :5433 (tests/conftest.py's
    # TEST_PG_BASE), so running the suite never disturbs your dev data.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese"
    db_echo: bool = False
    # How many database connections one backend process may hold. SQLAlchemy's
    # own defaults (5 + 10) size a pool for a thread-per-request server; this is
    # one asyncio process where every in-flight request holds a connection at
    # the same time, so the ceiling is 「同时在飞的请求数」, not 「worker 数」. A
    # single page load fans out dozens of them, and a request that cannot get a
    # connection within `db_pool_timeout_s` raises TimeoutError — a 500 on a
    # perfectly healthy database.
    db_pool_size: int = 20
    db_max_overflow: int = 30
    db_pool_timeout_s: float = 30.0
    # Hand out a connection only after checking it is still alive: a pooled
    # asyncpg connection that the database (or anything in between) closed while
    # idle otherwise fails the first statement of whoever checks it out next.
    db_pool_pre_ping: bool = True

    # --- Migration timeouts (#356) ---
    # Bound how long a migration waits on a lock / runs, applied by alembic's
    # env.py to the single connection every `upgrade head` uses. A migration
    # whose ALTER cannot grab its ACCESS EXCLUSIVE lock within this window fails
    # fast — the deploy goes red and retries — instead of blocking behind a live
    # backend's open transaction until the deploy's 30-minute budget is spent,
    # which starved cheese-dev's only runner slot and browned out the whole box
    # in #356. lock_timeout is the actual fix; keep it short (a few seconds).
    # PostgreSQL syntax: "10s", "500ms", or a bare integer (milliseconds).
    migration_lock_timeout: str = "10s"
    # Total per-statement ceiling, INCLUDING lock wait. Deliberately "0" (no
    # limit) by default so a legitimately long table rewrite is never killed
    # mid-migration; lock_timeout already caps the pathological case (waiting on
    # a lock we will never get). Ops can tighten it per deployment if wanted.
    migration_statement_timeout: str = "0"

    # --- 主仓产品配置并入 (fusion merge I3-config): fields main's product
    # domains (avatars/materials/storage/auth) read from settings. Superset so
    # the adopted product routes boot. Defaults mirror deploy/.env.prod.example.
    # Public origin of THIS merged backend (it serves /avatars itself) — used to
    # build absolute avatar URLs in notification DTOs. Default matches `task dev`
    # (:8081, the one dev port — see README "Quick Start"); the seeded demo on
    # :8799 and every deployment override it via env.
    avatar_base_url: str = "http://127.0.0.1:8081"
    storage_type: str = "local"
    storage_local_path: str = "./uploads"
    storage_local_url: str = "/uploads"
    # main's auth (real SRP/JWT login — A3): the merged app uses this as the
    # canonical identity. jwt_secret signs/verifies the product's access tokens.
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"
    # "This process was started by the deploy compose file" — a fact that does NOT
    # travel through the box's env file (#439). It is a literal in the compose
    # `environment:` block, beside STORAGE_LOCAL_PATH and HOME, so it survives the
    # one failure `environment` cannot report: when the env file does not apply,
    # `environment` falls back to "development" and every deployment check that
    # trusts it silently switches itself off. Never set this by hand; nothing but
    # the compose file may claim it.
    deployed_via_compose: bool = False
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
    # --- LLM gateway admin (L1/L2 — defined in `app.domain.agent.gateway`) ---
    # When the pool routes through the self-hosted LiteLLM gateway, the backend can
    # use the gateway's ADMIN API to (L1) mint a per-project virtual key — injected
    # into the sandbox instead of the master key, so a sandbox never holds admin
    # credentials and spend is attributable per project — and read back REAL token
    # usage from /spend/logs (fixes a provider-reported usage=0), and (L2) set a
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
    #
    # Still governs: AgentWorkRunner's outer transport-independent wrap for the SDK
    # backend (no activity signal exists there), plus the generic outer default
    # any backend keeps until it signals its own ceiling. The hooks-driven
    # backends no longer use this for their
    # effective timeout: they run the two-layer idle-suspect / hard-ceiling loop
    # (agent_idle_suspect_s / agent_turn_hard_ceiling_s below) and reschedule the
    # outer wrap to their own ceiling (turn 活跃度检测, 2026-08-09).
    agent_turn_timeout_s: float = 900.0
    # 冷启动看门狗: a turn that has emitted no assistant text and made no tool
    # call within this many seconds is declared dead, whatever its ceiling says.
    # It answers "did this turn ever start?", which `agent_turn_timeout_s` cannot
    # — silence and hard thinking look identical from the runner, so a turn whose
    # sandbox never came up used to hold 进行中 for the full 900s (dev, 2026-08-12:
    # every topic at once, `tools=0`, 30 minutes of platform-wide silence).
    # Generous on purpose: this must never cut a slow-but-live turn, only one
    # that never started. 0 disables it.
    agent_first_output_timeout_s: float = 300.0
    # Two-layer safety net for the hooks-driven backends (they share one
    # policy). Below this much idle time (no hook, and no
    # backend-specific activity signal) a turn is normal; past it the turn is only
    # SUSPECTED wedged and gets one lightweight liveness probe (a process-tree
    # probe over the link) rather than being killed outright
    # — a long foreground command with no interim hook must not look identical to a
    # dead screen.
    agent_idle_suspect_s: float = 300.0
    # Unconditional backstop for both hooks backends regardless of activity — guards
    # against a pathological "looks active but never converges" turn (a tool
    # retrying forever, a genuine infinite loop that keeps printing).
    agent_turn_hard_ceiling_s: float = 10800.0
    # The harness monitor's own backstop, and deliberately far larger than the
    # one above. That one is a real deadline for a turn that has stopped calling
    # tools, and it refreshes on every tool call. This one fires against a
    # session that `confirm_alive` keeps reporting alive, which is what a long
    # foreground command looks like from here (a 20-minute test run emits no
    # interim hook), so it must not be the number that ends a turn the probe
    # just said was healthy. It exists for the case where the probe itself has
    # stopped meaning anything.
    agent_session_ceiling_s: float = 86400.0

    # RETIRED (2026-08-10). Used to name a HOST directory holding a `cheese` CLI
    # to mount over the image's baked copy — but nothing kept that checkout in
    # sync with the backend, so boxes served agents a months-old CLI. The CLI is
    # now staged per-topic from the backend's own copy (ws.session_dir →
    # bin/cheese), which is host-visible and fresh by construction. Kept only so
    # a box that still sets it gets the loud startup warning in main.lifespan
    # instead of silence; delete once no deployment sets it.
    sandbox_shim_host_dir: str = ""

    # --- Subscription compute through the metering proxy ---
    # A turn on the subscription does NOT go through the LLM gateway: there is no
    # per-call API key to meter, and re-originating the request from our own HTTP
    # client would change what the provider sees. The meter is instead a proxy the
    # traffic passes through — same observability, different place.
    #
    # OFF by default: with no proxy configured a sandbox would resolve
    # api.anthropic.com to nothing and every turn would fail. Turning this on is a
    # deployment decision that needs the proxy actually running.
    subscription_enabled: bool = False
    # Address the SANDBOX reaches the metering proxy at. The docker bridge address
    # (not loopback, which no container can reach; not 0.0.0.0, which would put the
    # subscription on the LAN).
    subscription_proxy_host: str = "172.17.0.1"
    subscription_proxy_port: int = 8443
    # The metering proxy's CONNECT (regular-mode) listener. Containers are steered
    # by --add-host on 443; a DEVICE screen is a bare process with no root and no
    # docker, so its `claude` reaches the proxy via HTTPS_PROXY instead — that env
    # needs a listener that speaks CONNECT, which reverse mode does not.
    subscription_proxy_connect_port: int = 8444
    # Host a DEVICE reaches the CONNECT listener at. Empty = subscription_proxy_host,
    # which must resolve from every enrolled device's network. Otherwise configure
    # a tunnel; an unreachable direct address fails on connect (loud, not silent).
    subscription_device_proxy_host: str = ""
    # Where a device reaches the tunnel (`wss://…/llm/tunnel`), when it
    # cannot reach the CONNECT listener directly. On the ghg network it cannot:
    # measured 2026-08-14, packets to the box's listener port never reach its NIC,
    # dropped at a hypervisor bridge the box can neither see nor change — while
    # the gateway path those machines already use for the connector works and
    # carries websockets. Set this to that path and device subscription turns ride
    # it instead. Empty = no tunnel, and a device falls back to dialling
    # `subscription_device_proxy_host` directly (right for a flat network, and the
    # behaviour every deployment has today).
    subscription_tunnel_url: str = ""
    # Loopback port the machine-side helper listens on. Fixed rather than chosen
    # per launch: `claude` reads HTTPS_PROXY once at startup and a screen is
    # reused across turns, so a port that moved between launches would leave an
    # adopted session pointed at a helper that no longer exists.
    subscription_tunnel_local_port: int = 8445
    # Where the proxy's own CA and the ccproxy CA are mounted from. The sandbox
    # must trust the metering proxy (it terminates TLS) — an untrusted CA fails as
    # an opaque TLS error far from its cause.
    subscription_ca_host_path: str = ""
    # The proxy CA as a path THIS backend process can read (subscription_ca_host_path
    # is a HOST path handed to `docker -v`; the backend container usually cannot open
    # it). The device launcher embeds the CA bytes into the launch script, so device
    # subscription turns need this set — the deploy overlay mounts the proxy's cert
    # read-only and points this at it.
    subscription_ca_backend_path: str = ""
    # The `.credentials.json` that makes Claude Code run as a subscription client.
    # ROTATES ON EVERY REFRESH and a failed refresh writes it back EMPTY, which
    # permanently kills the subscription — so it is copied per sandbox and
    # promoted back only after a run that kept it valid, never shared live.
    subscription_credentials_host_path: str = ""
    # Token ceiling over a rolling window, enforced at the proxy (0 = no cap).
    # Enforced BEFORE forwarding: a cap that only reports the overspend is not a cap.
    subscription_token_cap: int = 0
    subscription_cap_window_s: int = 5 * 3600
    # The metering proxy's usage.jsonl as mounted in THIS container (issue #218):
    # subscription turns are metered there, and this is the one place the numbers
    # exist. Empty = no ingestion (deployments without the proxy). The compose
    # subscription overlay mounts the proxy's log dir read-only and sets this.
    subscription_usage_log: str = ""
    subscription_ingest_interval_s: int = 30

    # --- Self-hosted / BYO device compute (P3, fusion-design §5) ---
    # Public base URL a device reaches the backend at: the enrolled machine's
    # `cheese-hook` POSTs Claude Code hooks to
    # `{connector_public_base}/connector/hooks/{key}`, and the device-flow approval
    # link is built from it. For a NAT'd device this must be publicly reachable
    # (outbound-only for the link WS; the hook POST is a normal outbound request).
    # Every machine-facing URL is `{base}/<backend path>`, so the base must map
    # 1:1 onto the backend's ROOT. Behind a reverse proxy that strips an `/api`
    # prefix, that means the base ends in `/api` — otherwise `/sandbox/hooks/...`
    # lands on the SPA, which answers 200/405 and drops every agent event
    # silently (dev, 2026-08-08: the machine worked, the platform saw nothing).
    connector_public_base: str = "http://localhost:8099"
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

    # --- MicroCloud: cloud nodes for the team's compute pool ---
    # A project remains the billing/audit unit for each machine, but enrollment
    # binds the resulting device to that project's team. Cheese is one MicroCloud
    # tenant and keeps the opaque provider secret server-side. Empty secret = the
    # feature reports itself unavailable without breaking self-hosted compute.
    microcloud_base_url: str = ""
    microcloud_tenant_secret: str = ""
    microcloud_timeout_s: float = 30.0
    # The machine's built-in AI channel (the tenant console's →ccproxy button).
    # Sent in the create call (micro-cloud#78), so the machine is born on it;
    # the enrollment sweep still switches any machine that came up on another
    # channel — MicroCloud's default without the field is newapi, whose default
    # routes to a cheap non-Claude model. "" = leave whatever MicroCloud does.
    microcloud_ai_mode: str = "ccproxy"
    # Pin a specific granted offering (machine type + zone + template); 0 = take
    # the first active one, which is right while a tenant is granted exactly one.
    microcloud_offering_id: int = 0
    # Requested spec. Every value is clamped into the chosen offering's own
    # range, so these are preferences, not guarantees.
    microcloud_default_cores: int = 2
    microcloud_default_memory_mb: int = 4096
    microcloud_default_disk_gb: int = 20
    microcloud_login_user: str = "cheese"
    # An operator's SSH public key, authorised on every machine the platform
    # opens, next to the one-shot bootstrap key. That key is erased the moment
    # enrollment succeeds, so without this nobody can read a Cloud machine's
    # connector journal afterwards — which is why the 2026-08-29 failure on
    # machine 477 was never diagnosed. Platform-provisioned machines only: a
    # self-hosted box is someone else's and never gets a key of ours.
    microcloud_operator_ssh_pubkey: str = ""
    # The billing project's fund account, and the balance kept in it. MicroCloud
    # bills compute against this; 0 disables top-ups (an operator funds it by hand).
    microcloud_account_name: str = "compute"
    microcloud_initial_funds: float = 1000.0
    # A ceiling per project: provisioning is one API call, and nothing else here
    # stops a loop from filling a Proxmox node.
    microcloud_max_machines_per_project: int = 2
    # How long a SETTLED machine may go without being re-checked against
    # MicroCloud. Zero would put a provider round-trip on every read; never
    # would let a machine destroyed upstream sit here as `running` forever
    # (which happened, and also consumed the per-project limit).
    microcloud_reconcile_interval_s: float = 120.0
    # How often to sweep for machines that came up and still need enrolling as
    # devices (and switching to the AI channel above). Its own switch, NOT the
    # project scheduler's: that one spends model budget on 定期巡检 and ships
    # off, and machines must not depend on it. Ten seconds, not sixty: a Cloud
    # topic's first turn crosses this clock twice (running → switch the AI
    # channel, ready → enroll), and at 60s a person waited up to two minutes on
    # a timer for a machine that was already there. A tick with nothing
    # unsettled is three cheap queries.
    machine_enroll_interval_seconds: int = 10

    # --- ccproxy tenant realm: one revocable ticket per device (#420) ---
    # Cheese is one ccproxy tenant (micro-teams/ccproxy). Registering a device
    # there mints it a machine identity whose fake ticket ccproxy alone can
    # swap for real credentials — so removing the device revokes exactly that
    # device, instead of rotating a credential every box shares. Empty secret =
    # the feature reports itself unavailable; devices keep whatever
    # `ccproxy_upstream` an admin set by hand.
    ccproxy_tenant_base_url: str = ""
    ccproxy_tenant_secret: str = ""
    ccproxy_tenant_timeout_s: float = 30.0

    # --- Agent sandbox (spec §9.1: 每话题在隔离容器里跑 claude + 原生工具) ---
    sandbox_image: str = "cheesex-agent-sandbox:latest"
    # Machine quality gates use a disposable sibling container and never the
    # backend process. Keep this explicit so operators can ship a test-toolchain
    # image without granting the gate Docker socket or backend credentials.
    quality_gate_image: str = "cheesex-agent-sandbox:latest"
    quality_gate_memory_mb: int = 2048
    quality_gate_cpus: float = 2.0
    quality_gate_pids_limit: int = 512
    # Base URL the in-container `cheese` CLI calls back to (host → backend).
    # The app ROOT, with no `/api`. The in-container `cheese` CLI reaches the
    # backend port DIRECTLY (no gateway, so nothing strips a prefix), and since
    # #370 step 2 the platform routes are bare — `{base}/projects/…`.
    #
    # A box whose .env still carries the old `…/api` value keeps working:
    # `agent_api_base()` strips one trailing `/api` and the boot warning names
    # the box so it can be cleaned up. Silently 404ing every `cheese` call would
    # look exactly like an agent that decided not to use its tools.
    sandbox_api_base: str = "http://host.docker.internal:8099"
    # Shared secret the sandbox `cheese` CLI sends (X-Cheese-Token) so the
    # cheese write-API isn't open on the bind address. Empty → derived from
    # `jwt_secret` (see `sandbox_signing_secret`); pin it to rotate the two
    # independently, or to share one secret across multiple backend hosts.
    sandbox_token: str = ""

    @property
    def sandbox_signing_secret(self) -> str:
        """The HMAC secret behind every scoped sandbox token.

        `sandbox_token` when pinned; otherwise DERIVED from `jwt_secret` rather
        than randomised per process. That fallback used to be
        `secrets.token_hex(24)`, and the cost was not theoretical: a box's hook
        token is baked into the environment of the long-running `claude` at
        launch and never refreshed, so a fresh per-process secret invalidated
        every existing box's token the instant the backend restarted. The whole
        deployment went deaf at once — hooks 401ing into nothing, turns running
        to their ceiling reporting `tools: 0` while the agent inside worked
        perfectly — recovering only by destroying each box (and with it the
        session that IS that topic's conversational continuity).

        Deriving instead of randomising makes the secret stable across restarts
        with no deploy change, and `jwt_secret` is the right root because a
        deployment is already forced to pin a real one
        (`_require_real_jwt_secret_on_deployment`). Hashed with a domain
        separator so this value can never be replayed as a session JWT key, and
        so a future rotation of one does not silently rotate the other.
        """
        pinned = self.sandbox_token.strip()
        if pinned:
            return pinned
        return hashlib.sha256(
            b"cheesex:sandbox-signing-secret:v1:" + self.jwt_secret.encode()
        ).hexdigest()

    def agent_api_base(self) -> str:
        """`sandbox_api_base` with a stale trailing `/api` removed.

        The CLI talks to the backend port directly, so its base is the app root.
        It used to be the root plus `/api`, because the platform routes carried
        that prefix; #370 step 2 flattened them. Normalising here means a box
        that has not updated its .env keeps working instead of having every
        platform action 404 — a failure that reads as "the agent chose not to
        use its tools", which is the worst possible way to learn about it.
        """
        base = self.sandbox_api_base.rstrip("/")
        return base[: -len("/api")] if base.endswith("/api") else base

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
    # Container lifecycle is deterministic maintenance and must keep running
    # even when model-consuming automatic heartbeats are disabled.
    # A room now outlives the work done in it, so boxes accumulate per ROOM
    # rather than draining as topics close. Hours, not days: container count
    # should track work in flight, and a box whose room nobody has touched
    # since yesterday is paying rent for a conversation that will resume from
    # its transcript anyway.
    #
    # 8 hours holds for a whole room (2026-08-17 decision), because what
    # "idle" MEASURES is the room's last activity AND its tasks'. Judging the
    # room alone would tear down live work the moment the room's own timeline
    # went quiet — and a room whose work has been split out is quiet by design,
    # so that is the normal case.
    sandbox_reap_interval_seconds: int = 3600
    sandbox_idle_hours: float = 8
    # Seconds between orphan sweeps (AgentWorkRunner.sweep_orphans). On by default,
    # unlike the heartbeat above: it consumes no model calls unless it actually
    # finds a killed turn, and its whole purpose is catching the case where
    # nothing else will ever look — a turn dying without the process dying.
    orphan_sweep_interval_s: int = 300
    # How long a registered turn may produce nothing — no block, no frame —
    # before the sweep calls it wedged and tears it down. See
    # AgentWorkRunner.SILENT_TURN_S for why 30 minutes and not less.
    turn_silence_timeout_s: float = 1800.0
    # How long a topic may sit on a mid-turn block before `/topics/{id}/status`
    # calls it stalled. Lower than the sweep's ceiling above on purpose: this
    # one only REPORTS, so a false positive costs a second look rather than a
    # cancelled turn, and 10 minutes is already past the ceiling of a single
    # blocking tool call — the longest a healthy turn can legitimately go
    # without adding a block.
    turn_stall_signal_s: float = 600.0

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

    # --- GitHub App (cheesex-app, #188 minimal / #192 git integration) ---
    # The platform's GitHub credential: the backend holds the App private key
    # and mints short-lived installation tokens from it. Unset = the
    # /sandbox/github-token endpoint answers "not configured"; nothing else
    # changes.
    github_app_id: int | None = None
    github_app_private_key_path: str | None = None
    # Which installation to mint a token for is resolved per-project via the
    # project_git_installations table (#192), not a config value — a
    # deployment can have many connected repos, each with its own
    # installation_id.
    github_app_slug: str = "cheesex-app"
    # 采纳即合并 (docs/accept-is-merge.md #296, staged rollout): submitting an
    # accept card opens a real PR with the App's installation token; 采纳 merges
    # that PR via the API. On by default as of stage 1 — the App owns PR
    # creation, so the accept path never opens a competing PR while this is on
    # (see AcceptService.accept). Submission-side only — accept dispatches on the
    # card's stored pr_number, so flipping this never strands a card, and a
    # deployment can still switch it off via .env (dev override) if needed.
    accept_via_pr: bool = True
    # Tier-2 semantics for the accept poller (#468): check names that must have
    # APPEARED (and be green) before the poller may merge. Absence is pending,
    # never pass — #465 merged on a run where `test` was never triggered and
    # everything visible was skipped/green. Comma-separated; empty disables.
    #
    # Each entry may carry the diff scope that makes it required:
    # `name:glob;glob` (globs are GitHub's path-filter syntax — `**` crosses
    # directories, `*` does not). A bare name is required unconditionally.
    # **Mirror the workflow's own `paths:` filter here.** `test` lives in
    # .github/workflows/test.yml, which only triggers on `backend/**` — so on a
    # frontend-only PR that check never appears, and demanding it unconditionally
    # is an infinite wait, not a safety valve (2026-08-16: #483/#485/#486 sat
    # fully green until a human merged them by hand). Getting the scope too
    # NARROW is the mild failure: a check that does run still has to go green,
    # because `check_state` sees it — only the not-yet-created window reopens.
    accept_required_check_names: str = "test:backend/**;.github/workflows/test.yml"
    # Backstop for the roster above: how long a required check may stay MISSING
    # before the card stops waiting and asks a human. Waiting with no timeout is
    # how a renamed/disabled workflow — or an Actions billing lapse, which this
    # org had on 2026-08-13 — turns into a card that hangs forever with nobody
    # told. The exit is 交给人, never an auto-merge. 0 disables (wait forever).
    accept_required_check_grace_minutes: int = 30

    # --- 闸门孤儿卡扫底 (2026-08-11) ---
    # How often to look for `pending_gate` cards nobody will ever settle (the
    # gate runner is an in-memory asyncio task — see review/gate_sweep.py for
    # the three ways it goes missing). Startup does one sweep unconditionally;
    # this interval is what covers the "process still alive, task died" half.
    # 0 disables the periodic sweep (the startup one still runs).
    gate_sweep_interval_s: int = 300

    # --- 两阶段采纳 (PR迭代式, 2026-08-09) ---
    # How often the background poller checks an open PR's CI / the deploy
    # workflow it triggers after merge.
    accept_pr_poll_interval_s: int = 60
    # 后端报错回房间 (issue #283): how often to close expired burst windows so a
    # flood that STOPPED still reports how big it was. Only bounds how late that
    # summary line is — the dedup window decides whether it exists. 0 disables.
    backend_error_flush_interval_s: int = 60
    # 自动同步上游: how often to pull the upstream's default branch into each
    # linked project's base. Falling behind is what makes accepts unable to push
    # (see SchedulerService.sync_upstreams), so this only has to run often
    # enough that the gap stays small — not on every commit. 0 disables it.
    upstream_sync_interval_s: int = 1800
    # --- notifications and deadlines ---
    # Three jobs nothing in a request path can do. An aggregation window that
    # never closes is a notification written and never delivered; an undrained
    # email queue is an inbox that never receives; an unswept deadline is a
    # promise the platform made and quietly did not keep. Each failure is
    # silent, which is why the intervals are on by default. 0 disables one.
    notification_finalize_interval_s: int = 60
    notification_email_drain_interval_s: int = 60
    task_deadline_sweep_interval_s: int = 900
    # --- 结论卡 (2026-08-11) ---
    # How often open conclusion cards past their absolute deadline are swept and
    # auto-accepted. Backstop for the turn-end hook: 默认采信 must not depend on
    # the parent's digest turn ever running. 0 disables the loop (tests).
    conclusion_sweep_interval_s: int = 60
    # merge_method for the auto-merge (GitHub: merge | squash | rebase). MUST
    # be one the target repo actually allows — GitHub answers 405 forever for
    # a disabled one, which is exactly how 两阶段采纳 shipped never having
    # merged once (hardcoded "merge" against a squash-only repo). Configurable
    # rather than hardcoded so a differently-configured repo isn't a code
    # change; deliberately NOT auto-retried with another method, since 405 also
    # means draft PR / branch protection and silently switching would both mask
    # those and produce merge commits in repos that allow several methods.
    accept_pr_merge_method: str = "squash"

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
    # #192 "连接 GitHub 账号": the cheesex-app GitHub App's own user-to-server
    # OAuth credential — deliberately separate from oauth_github_client_id
    # (the login provider above), even though it's the same authorize/token
    # endpoints. Add "github_app" to oauth_enabled_providers to turn this on.
    oauth_github_app_client_id: str | None = None
    oauth_github_app_client_secret: str | None = None
    oauth_github_app_redirect_url: str | None = None

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
    # Escape hatch for LOCAL harnesses only (the eval runner's throwaway backend,
    # browser probes): admit a chat WebSocket that carries no ``?token=`` and let
    # the message body name its own author. That is the pre-token Phase-0 path —
    # with it on, any client can post as any handle, which is why production
    # leaves it off. It does NOT weaken the invalid/expired-token case: a socket
    # that presents a token we cannot verify is refused either way, because
    # silently downgrading a failed credential to "anonymous" is what let a whole
    # batch of messages land under 匿名者 while the sender saw no error at all.
    chat_ws_allow_anonymous: bool = False

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
    pdf_import_max_pages: int = Field(default=20, ge=1, alias="PDF_IMPORT_MAX_PAGES")
    pdf_import_max_concurrency: int = Field(
        default=3, ge=1, le=10, alias="PDF_IMPORT_MAX_CONCURRENCY"
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

    # Which build is running. Baked into the image at build time (Dockerfile
    # ARG GIT_SHA → ENV APP_VERSION), so the image is self-describing — a stale
    # or mis-tagged deploy can't lie about its version. "dev" for a local run.
    app_version: str = "dev"
    # 内测: show the running commit sha in a corner of the UI, so a tester can
    # confirm at a glance which build they're on. Off by default (prod); the
    # dev/test box's .env sets it true. The frontend reads it from /api/version.
    show_version_badge: bool = False

    @model_validator(mode="after")
    def _require_real_jwt_secret_on_deployment(self) -> "Settings":
        """Fail the boot when a deployment left JWT_SECRET at its insecure default.

        ``jwt_secret`` signs AND verifies every session token. Its field default
        ``"dev-secret"`` exists only so local dev and the test suite need zero
        config. On a real deployment that default is a live hazard on two counts:

        - Anyone can forge a valid token, because the signing key is a constant
          sitting in the source tree.
        - The #342 failure: if the pinned real secret fails to load for one
          process (an env-not-applied deploy window like #356), the process
          silently boots on ``dev-secret``. The moment the real secret comes
          back, every token signed with ``dev-secret`` in between fails
          verification and every logged-in user is signed out — with no error
          logged anywhere (24/24 401 in #342), recovering only as tokens expire.

        So a deployment MUST provide a real secret; there is no deployment where
        the default is acceptable. "Deployment" is answered by TWO independent
        signals, and needing two is the point (#439):

        - ``deployed_via_compose`` — a literal in the deploy compose file, which
          does not travel through the box's env file. This is the authority,
          because it is the only one that survives the env-not-applied window
          described above. Under it, ``environment`` saying "development" is
          evidence the env file failed, not evidence this is a dev box.
        - ``environment`` outside dev/test — the line the rest of the app already
          draws (secure cookies, the X-User-Id gate). Still checked, for any
          deployment that does not run through this compose file.

        Local dev and the test suite set neither, keep the default secret and
        never trip this, which is why fail-closed does not take the suite down.

        Mirrors #338's treatment of SANDBOX_TOKEN — make the empty/default case a
        loud, boot-time event rather than a silent runtime one — but crashes the
        boot instead of only warning: an unpinned SANDBOX_TOKEN is benign on an
        app-only box, whereas an insecure JWT_SECRET is wrong on every deployment.
        """
        if self.jwt_secret.strip() and self.jwt_secret != "dev-secret":
            return self

        # RuntimeError, not ValueError, in both branches below: a ValueError here
        # is wrapped by pydantic into a ValidationError whose repr dumps the whole
        # input dict — which on a real deployment carries the DB password, API
        # tokens and other live secrets straight into the crash log. A plain
        # RuntimeError propagates unwrapped, so the boot dies on this one message
        # and nothing else. (#338: keys never go into logs.)
        generate = 'python -c "import secrets; print(secrets.token_urlsafe(48))"'

        # #439: `deployed_via_compose` is checked BEFORE `environment`, and this
        # ordering is the whole fix. Both `ENVIRONMENT` and `JWT_SECRET` come from
        # the box's env file, so the env-not-applied window this guard exists for
        # takes out both at once: the secret falls back to `dev-secret` and
        # `environment` falls back to `development`, whereupon the check below
        # would wave it through — the fuse and the line it protects running off one
        # supply. The compose literal cannot fall back, so when it is set we know
        # this is a deployment no matter what `environment` claims.
        if self.deployed_via_compose:
            raise RuntimeError(
                "JWT_SECRET is missing, empty, or the built-in 'dev-secret' "
                "default, in a process started by the deploy compose file "
                f"(ENVIRONMENT reads '{self.environment}'). If that says "
                "'development' on a deployed box, the env file did not apply and "
                "this is exactly the #342 window: booting on the default silently "
                "invalidates every session on the next restart that loads the real "
                "secret, logging every user out with no error. Fix the env file "
                f"rather than this check. Generate a secret with: {generate}"
            )

        if self.environment in ("development", "test"):
            return self

        raise RuntimeError(
            "JWT_SECRET must be set to a real secret when ENVIRONMENT is "
            f"'{self.environment}' (i.e. not development/test); it is "
            "currently missing, empty, or the built-in 'dev-secret' default. "
            "Booting on the default silently invalidates every session on the "
            "next restart that loads the real secret — every user is logged "
            f"out with no error (#342). Generate one with: {generate}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
