"""Application configuration loaded from environment / .env."""

import base64
import binascii
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The gateway mounts this whole app under `/api` and strips that one segment
# (`proxy_pass http://backend:8081/`), so a backend route is bare while the URL
# the browser used is `/api` + that route. Anything the BROWSER will resolve
# against — a cookie's Path, a rewritten asset URL, the base a dev server has to
# be started under — has to be built with this. It lives in core rather than in
# the API layer because the device launcher needs the same string to tell an
# agent where its app will be mounted, and the domain cannot import the API.
GATEWAY_MOUNT = "/api"

# What an unconfigured development machine or test run encrypts with. Public
# by construction, so a deployment may never use it; see
# `Settings._require_data_encryption_key`.
DEVELOPMENT_DATA_ENCRYPTION_KEY = "Y2hlZXNlLWRldmVsb3BtZW50LWRhdGEta2V5LTAwMDA="


def parse_data_encryption_keys(value: str) -> tuple[bytes, ...]:
    """DATA_ENCRYPTION_KEY as raw 32-byte keys; empty when it is unset.

    Raises ValueError naming what is wrong with an entry, never the entry.
    """
    entries = [entry.strip() for entry in value.split(",")]
    if entries == [""]:
        return ()
    keys: list[bytes] = []
    for position, entry in enumerate(entries, start=1):
        try:
            key = base64.urlsafe_b64decode(entry.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError):
            key = b""
        if len(entry) != 44 or len(key) != 32:
            raise ValueError(
                f"DATA_ENCRYPTION_KEY entry {position} is not the base64url "
                "encoding of 32 bytes (44 characters)."
            )
        keys.append(key)
    return tuple(keys)


class Settings(BaseSettings):
    # `validate_by_name` exists for exactly one field, `platform_admin_handles`:
    # it carries a `validation_alias` (so the deploy keeps working through the
    # env-name change), and in pydantic a field with an alias is otherwise
    # reachable ONLY by that alias — `Settings(platform_admin_handles=[...])`,
    # which the unit tests and any in-process caller write, would be silently
    # dropped by `extra="ignore"` and read back as the empty default. The flag
    # is per-model, not per-field, but no other field here has an alias, so for
    # them it changes nothing.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
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
    #
    # The number below the pool must also fit is the SERVER's, which nothing in
    # this process can see. THREE pools share it during an ordinary release: the
    # outgoing backend, the incoming one the rollout brings up beside it, and the
    # separately released connection owner. At 20+30 each that is 150 against a
    # `max_connections` of 100, and on 2026-09-16 it landed exactly as the
    # arithmetic predicts — 19 seconds after a new backend answered /healthz,
    # PostgreSQL refused 83 connections in one minute with `sorry, too many
    # clients already`, and every request in flight failed with it. Users read
    # that as the rooms mysteriously 401-ing and recovering, several times a day,
    # once per deploy.
    #
    # So the ceiling is per-process but the budget is shared: the two backends
    # at (size + overflow) each, plus the connection owner's own pool, have to
    # leave room for the migration the deploy runs and for anyone holding a
    # psql. The owner registers devices and answers bindings; it never fans out
    # the way a page load does, so the compose file hands it DB_POOL_SIZE=5 and
    # DB_MAX_OVERFLOW=5 and the backends take the rest. Each backend also holds
    # one connection outside its pool for the owner lock (`core.ownership`):
    # 2 x (35 + 1) + 10 + 10 = 92 of the 97 a default PostgreSQL offers once its
    # superuser reserve is taken out (tests/unit/test_db_pool_fits_the_server.py
    # holds this arithmetic). A
    # box whose server is configured larger can raise these; a box that adds a
    # fourth pool has to lower them. dev's server was raised to 200 on
    # 2026-09-18 (conf.d/10-connections.conf on cheese-dev-env1-postgresql).
    db_pool_size: int = 20
    db_max_overflow: int = 15
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
    # A single uploaded attachment's ceiling in bytes — every path that puts
    # bytes into the attachment table goes through `AttachmentService.upload`,
    # which reads this one value, and `GET /attachments/limits` reports that
    # same value, so what the browser is told is what it will be refused for.
    # The default is the deployment's existing ceiling: the frontend nginx
    # `client_max_body_size 100M` on `/api/` (frontend/nginx.conf), so nothing
    # a browser could already send starts being refused. That nginx line is a
    # separate number in a separate file: raise one and raise the other.
    attachment_max_bytes: int = 100 * 1024 * 1024
    redis_url: str = "redis://localhost:6379/0"
    # Per-client request limits (`app/core/request_limits.py`). Measured on the
    # shared deployment, a normal browser peaks at 14 requests in flight, 14/s
    # and 111/min — a page fans out one request per project — so these sit
    # above that and below what one runaway page did to the whole process.
    request_rate_per_s: float = Field(default=20.0, gt=0)
    request_rate_burst: int = Field(default=100, ge=1)
    request_concurrency: int = Field(default=16, ge=1)
    request_queue_depth: int = Field(default=64, ge=0)
    request_queue_timeout_s: float = Field(default=15.0, ge=0)
    # How long a rate check may wait on Redis before the request goes through
    # unchecked: a hung Redis must not hang every request with it.
    request_limit_redis_timeout_s: float = Field(default=0.25, gt=0)
    # The process that owns device WebSockets is released independently from the
    # business backend. Empty keeps the in-process hub for local development and
    # tests; deployed business backends point at the stable compose service.
    # Where an alert goes when something breaks that nobody is watching. A
    # Feishu group's custom-bot webhook URL; empty disables alerting entirely,
    # which is what a developer's machine and every test wants.
    feishu_alert_webhook: str = ""

    # Coordinated ingress cutover is a separate release. No implicit fallback.
    preview_connection_mode: Literal["legacy", "owner"] = "legacy"
    preview_connection_url: str = ""

    @property
    def preview_connection_auth_secret(self) -> str:
        return hashlib.sha256(
            b"cheesex:preview-owner-rpc:v1:" + self.jwt_secret.encode()
        ).hexdigest()

    device_connection_url: str = ""
    device_connection_secret: str = ""
    device_connection_owner: bool = False
    # Where the connection owner asks which connector build is published: the
    # backend that serves `/connector/latest/...`, the same files a machine's
    # self-update downloads. The owner is not restarted by a release, so its own
    # copy of those files lags them. Empty = this process serves them itself.
    connector_origin_url: str = ""

    @property
    def device_connection_auth_secret(self) -> str:
        """Stable internal credential without adding a second deploy secret."""
        return self.device_connection_secret or self.jwt_secret

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
    #: A person's mail server may resolve to a private or loopback address, and
    #: may be spoken to without TLS. Only for a local test mail server: on a
    #: deployment it would let anyone make the backend dial its own network.
    integration_allow_private_hosts: bool = False
    #: Remote MCP servers a `.mcp.json` names may be plain HTTP and on a private
    #: network. Only for a local test server: on a deployment it would let a
    #: project member make the backend dial its own network.
    remote_mcp_allow_private_hosts: bool = False
    #: Clients registered in advance with an MCP authorization server that
    #: supports neither Client ID Metadata Documents nor Dynamic Client
    #: Registration, keyed by the authorization server's issuer:
    #: {"https://as.example": {"client_id": …, "client_secret": …,
    #: "token_endpoint_auth_method": "client_secret_basic"}}. The redirect URI
    #: they were registered with is `{frontend_url}/api/mcp/oauth/callback`.
    remote_mcp_oauth_clients: dict[str, dict[str, str]] = {}
    # Where a mail host's real address is looked up when the local resolver
    # only hands out a proxy's fake-ip placeholder (198.18.0.0/15).
    integration_doh_url: str = "https://dns.alidns.com/resolve"
    # Dedicated content domain, outside the platform's registrable domain.
    # Empty until its wildcard DNS/TLS and host-preserving gateway are ready.
    sites_domain: str = ""
    sites_scheme: str = "https"
    sites_port: int | None = None
    # OAuth browser-flow landing pages (must match the frontend router).
    frontend_oauth_success_path: str = "/account/oauth/success"
    frontend_oauth_error_path: str = "/account/oauth/error"
    frontend_oauth_verify_path: str = "/account/oauth/verify"
    frontend_oauth_complete_path: str = "/account/oauth/complete"
    frontend_2fa_verify_path: str = "/account/verify-2fa"
    require_invite_code: bool = False
    jwt_secret: str = "dev-secret"
    access_token_expires_seconds: int = 15 * 60
    # A sign-in lasts this long from the moment it happened; refreshing does
    # not extend it.
    refresh_token_expires_seconds: int = 60 * 60 * 24 * 30
    # A sign-in nobody has refreshed for this long is over, however much of
    # its lifetime remains.
    refresh_idle_timeout_seconds: int = 60 * 60 * 24 * 14
    # How long a refresh token that was just rotated away is answered as a
    # second tab refreshing at the same moment: with an access token and no new
    # cookie, because the first tab's answer carries the successor.
    refresh_reuse_grace_seconds: int = 30

    # --- Agent (Claude Agent SDK) ---
    # The SDK talks to the model via the `claude` CLI. We route to a provider
    # through the Anthropic-compatible gateway (spec §9: 不绑定模型). For Zhipu
    # GLM: base_url=https://open.bigmodel.cn/api/anthropic, token=ZHIPU_API_KEY.
    # Swapping to LiteLLM / Anthropic later is just env, no code change.
    agent_model: str = "glm-5.2"
    # Which models each harness that does NOT speak the platform gateway may
    # be pointed at ({"codex": ["gpt-5.2"]}). A harness that speaks the gateway
    # needs no entry: everything the project can use is something it can drive,
    # and listing those again would be a second copy to fall out of date.
    agent_harness_models: dict[str, list[str]] = {}
    # 这套部署可用的骨架，按偏好排序（结论 28），例如 ["claude-code", "pi"]。骨
    # 架是开发者选项，不是产品概念：它不在类型上也不在实例上，普通用户看不到。项
    # 目可以在自己的设置里指定其中一个；没指定、或指定的不在这里，就从头往下取第
    # 一个那台机器挂着的。空着 = 注册表里未配置时的那一份；注册表是唯一写着骨架名
    # 字的地方（不变量 I5），所以这里给不出名字当默认值。列了注册表里没有的名字，
    # 启动就失败——悄悄跑另一个骨架，正是结论 28 要防的那件事。
    agent_harnesses: list[str] = []
    # Shared central session host; private scratch runs in isolated containers.
    agent_session_device_id: str | None = None
    agent_session_api_base: str | None = None
    # The memory one session on the session host may hold, in MiB; 0 lifts it.
    # Every room's session shares that machine's kernel, so one without a cap
    # can take the others into swap with it (#1544). It is also the room a new
    # session needs: a turn that would start one waits until the host has that
    # much available.
    agent_session_memory_max_mb: int = 3072
    private_chat_executor_image: str = "cheese-private-executor:2.1.282"
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    # 骨架设置，不是设计约束（结论 33）：活在房间里是平的，没有子卡，谁能开活也
    # 不受限，所以这个数字挡的是一台机器上同时跑多少层进程，不是平台认不认孙辈的
    # 活。部署想让一条活自己再往下派就抬高它，代码里没有一处跟着这个数字分岔。
    claude_code_max_subagent_spawn_depth: int = 1
    # Model aliases the CLI may resolve internally; map them to the provider.
    # Subagents (the Task/Agent tool) resolve via sonnet/opus → keep them on the
    # main model so 分身 don't silently run an older/weaker model.
    agent_haiku_model: str | None = "glm-4.5-air"
    agent_sonnet_model: str | None = "glm-5.2"
    agent_opus_model: str | None = "glm-5.2"
    # --- Platform-side fetch service (app.domain.fetch) ---
    # Reading the web for an agent happens HERE, not in the sandbox: the sandbox
    # has a datacentre egress that several sites refuse outright, one browser per
    # topic would mean one 185 MB Chrome per topic, and nothing cached would be
    # shared. Both endpoints are optional and both default to off.
    #
    # The reader is a THIRD PARTY: enabling it hands every fetched URL to someone
    # else, so it is an operator's decision rather than a default. Measured, it
    # earns its place — it returned a Cloudflare-challenged page and a host this
    # network cannot reach at all — but that is a trade to make deliberately.
    fetch_reader_endpoint: str | None = None
    # A shared browser, reached over HTTP rather than supervised in-process:
    # it needs Chrome and pooling, and an API worker cannot restart a headless
    # browser process tree cleanly. Measured, one shared instance served six
    # concurrent pages in 4.7s where per-caller browsers took 9.5s for three.
    fetch_browser_endpoint: str | None = None
    # A DNS-over-HTTPS resolver (JSON API), asked only on a machine whose own DNS
    # answers every name with a fake-IP placeholder (198.18.0.0/15): fetch has
    # to see the real address to know it is public. See domain/fetch/guard.py.
    fetch_dns_over_https: str = "https://223.5.5.5/resolve"

    # LibreOffice, reached over HTTP for the same reasons as the browser above:
    # it is ~800MB and wants a writable profile directory, which rules it out of
    # both the backend image and the sandbox image. It converts a Word or
    # PowerPoint deliverable to PDF so the room can show it instead of offering a
    # download. Unset, the preview panel says so and still hands the file over.
    office_render_endpoint: str | None = None

    # The office editor (OnlyOffice Document Server) people edit room files in.
    # Four addresses because three parties reach each other differently: the
    # browser loads the editor from `office_editor_url` (proxied by the
    # frontend's nginx); the backend fetches a saved document back from
    # `office_editor_internal_url`; the editor fetches and returns documents at
    # `office_editor_backend_url`. `office_editor_jwt_secret` is shared with the
    # editor's JWT_SECRET: it is what makes a save callback the editor's rather
    # than anybody's. Without the secret the editor is off and files stay
    # read-only previews.
    office_editor_url: str = "/office-editor"
    office_editor_internal_url: str = "http://cheese-office-editor"
    office_editor_backend_url: str = "http://backend:8081"
    office_editor_jwt_secret: str | None = None

    # The collaboration service that holds every room's living document live
    # (collab/ in the frontend package). The browser reaches it through the
    # frontend's /collab location with a ticket this backend signs; this
    # backend reaches it at `collab_internal_url` for writes that do not come
    # from an editor, and it reaches this backend to load and store documents.
    # `collab_secret` is shared with it and signs both. Without it the living
    # document cannot be opened or written.
    collab_internal_url: str = "http://collab:8902"
    collab_secret: str | None = None

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

    # --- Docs site (app/domain/docs_site) ---
    # Where 问芝士 reads the docs from: the frontend image serves the built
    # site, so the backend asks its own deployment for the same version readers
    # see. Unset, 问芝士 answers that it is unavailable.
    docs_index_url: str | None = "http://frontend/docs/ask-index.json"
    # The developer pages' index, behind the /docs/dev/ gate; the backend passes
    # it with an internal pass (docs_site/access.py). Agents read it only in
    # projects whose repository is one of `docs_dev_repositories`.
    docs_dev_index_url: str | None = "http://frontend/docs/dev/ask-index.json"
    # Projects working on this platform's own code ("owner/repo",
    # case-insensitive): their agents may read the developer docs, and their
    # members and agents may claim feedback (`FeedbackService.may_claim`).
    docs_dev_repositories: list[str] = ["SageSeekerSociety/cheese"]
    # The gateway model 问芝士 answers with. Its virtual key is minted through
    # `llm_gateway_admin_base`; what it spends is charged to the asker's
    # personal credits, so the model must be priced on the gateway.
    docs_assistant_model: str = "deepseek-flash"
    # Whether 问芝士 searches and reads the docs itself, over the three tools in
    # `docs_site/tools.py`, instead of answering from one round of retrieval.
    # False keeps the old path, for a deployment that wants the cheaper one.
    docs_assistant_agentic: bool = True
    # Answers in flight across one backend process.
    docs_assistant_concurrency: int = 8

    # --- A person's 芝士 outside any project (app/domain/agent/personal, #2285) ---
    # The gateway model it answers with; charged to the asker's personal
    # credits at what the gateway spent, so the model must be priced there.
    assistant_model: str = "deepseek-flash"
    docs_question_retention_days: int = 90
    # How long an admin's pass to /docs/dev/ lasts before it is re-issued.
    docs_dev_session_seconds: int = 3600

    # --- Topic naming (app/domain/topic/naming.py) ---
    # The platform names rooms itself, off the main agent's turn: a small model
    # through the gateway, on a virtual key of its own capped at this budget
    # per 30 days. Unset gateway admin credentials = the main agent names the
    # room with `cheese_title`, as before.
    topic_naming_model: str = "deepseek-flash"
    topic_naming_budget_usd: float = 10.0
    topic_naming_timeout_seconds: float = 15.0
    # A renamed room is re-judged no sooner than this, and at most this often a
    # day: a title is how people find a room again, so it moves rarely.
    topic_naming_follow_interval_seconds: int = 1800
    topic_naming_follow_daily_limit: int = 3
    # Messages since the last judgement that make a room worth looking at again
    # even without a signal (a task, an accept card, a changed goal).
    topic_naming_follow_messages: int = 30

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
    # Handles that are platform administrators — the people who run the admin
    # screens (members, models, stats, spaces, integrations). Feedback triage is
    # a separate roster, `feedback_triage_handles` below. JSON list in env,
    # e.g. '["alice","bob"]'.
    # A settings list rather than a role because no production path assigns
    # `SystemRole.SUPER_ADMIN` today — a role check would evaluate to "nobody"
    # and lock the surface for everyone.
    #
    # This is the **root** half of the admin list: the other half is the
    # `platform_admins` table (the 成员管理 screen), and the judge is their union
    # (`AdminService.admin_handles`). Rows added on the page can be removed
    # there; these cannot, because adding admins is itself an admin action — a
    # list you can empty from the UI is a door that locks from the inside.
    #
    # The env name is `PLATFORM_ADMIN_HANDLES`. It used to be
    # `FEEDBACK_ADMIN_HANDLES`, from when the only thing an admin administered
    # was feedback; that name is still read (a deployment's env file is not
    # something a code change can edit), so an unmigrated box keeps booting, but
    # the new name wins when both are set. Drop the old choice once the deploy
    # workflow and the boxes carry the new one.
    #
    # REQUIRED on a deployment: an empty list is not "no admins configured yet",
    # it is a feedback queue that accepts submissions and can never be worked —
    # and the users who submit cannot tell the difference from "nobody has
    # picked this up yet". `_require_platform_admins_on_deployment` fails the
    # boot instead. Local dev and the test suite keep the empty default.
    platform_admin_handles: list[str] = Field(
        default=[],
        validation_alias=AliasChoices(
            "PLATFORM_ADMIN_HANDLES", "FEEDBACK_ADMIN_HANDLES"
        ),
    )
    # Who works the feedback queue: the triage page (`/admin/feedback`), status
    # changes, internal notes, private and security reports, deleting other
    # people's reports and comments. A roster of its own, NOT the platform admins
    # above: private feedback is what people chose not to show everyone, and the
    # platform admins are a working group who need the admin screens for other
    # jobs. Being one does not make you a reader of every private report.
    #
    # Deployment config only — there is no page that adds to it, so nobody can
    # widen it from inside the product. JSON list in env, e.g. '["alice"]'. The
    # dev deploy writes it (`deploy-dev.yml`); another deployment sets it in its
    # own env file (`deploy/.env.prod.example`).
    #
    # Empty means nobody administers feedback: submissions still work and every
    # admin-only branch stays closed. Not a boot failure, unlike the platform
    # roster above: that one is the only way into the admin screens at all,
    # while this one only gates a queue whose readers are a choice. Mind the
    # name: `FEEDBACK_ADMIN_HANDLES` above is the OLD spelling of the PLATFORM
    # roster, which is why this one is not called that.
    feedback_triage_handles: list[str] = []
    # How many feedback PROPOSAL cards one topic may see per day. The cap exists
    # for the agent path (`cheese_feedback_propose`): a misfiring loop proposes
    # once per turn, and a number in settings is the difference between a bad
    # afternoon and a topic nobody can read. Proposal cards are the one kind of
    # "the next step is on a person" that nobody is waiting on, so unlike a
    # decision request it is safe to drop — and this is what drops it.
    feedback_proposals_per_topic_per_day: int = 2
    # How many reports ONE AUTHOR may publish per rolling 24 hours. This is the
    # human path, and it exists because deleting a spam report is cleanup, not
    # prevention: the only thing standing between a script and the public list
    # was a person noticing and pressing delete, one row at a time.
    #
    # Deliberately far above any honest day. Somebody working through a bad
    # release can file a dozen real bugs, and a cap that refuses *them* is the
    # failure this must not have — the message says which limit was hit and when
    # it lifts, so the one person this is aimed at is a runaway loop, not a
    # tester. Not shared with the proposal cap above: that one is per TOPIC and
    # protects a room's reading; this one is per PERSON and protects the list.
    feedback_reports_per_author_per_day: int = 30
    # Which registered profile is the platform default ("our AI pool"). Normally
    # "default" (the GLM pool). Set to "claude-opus"/"claude-fable" to run the
    # whole platform on the subscription seat — e.g. a demo where the GLM pool is
    # out of balance. The chosen profile must have credentials or boot fails fast.
    agent_default_profile: str = "default"
    agent_system_prompt: str = (
        "你是「芝士」，知是平台里的 AI 队友。你贯穿一个项目的全过程，"
        "了解项目的话题、决策和进展。用自然清楚的语言交流，"
        "根据读者补齐必要背景和陌生术语，少说废话。"
        "当你引用项目记忆里的事实时，自然地点明依据。"
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
    # any backend keeps until it signals its own ceiling. The session runtimes
    # do not use this for their effective timeout: they apply the liveness
    # rules the settings below describe (process gone, no-progress, unread
    # grace; the ceiling only records) and hand the outer wrap their own
    # ceiling through the `turn_ceiling` frame.
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
    # The wall-clock mark past which a turn is recorded as long. A metric, not
    # a gate: crossing it is written to the turn record (`ceiling_crossed_s`),
    # and nothing ends. With the three gates in place (the session's process or
    # runner gone, output with no progress, an input unread), what a wall clock
    # alone could still end is a turn that is working and has not finished,
    # which is not a fault. The outer wrap in runtime.py receives it via the
    # `turn_ceiling` frame.
    agent_turn_hard_ceiling_s: float = 10800.0
    # How long a message we injected may sit unconsumed before the session is
    # called unable to read. On a different axis from the others: they watch
    # what a session PRODUCES, and a session that has stopped reading goes on
    # producing, so none of them ever fires for it. This one only exists
    # while something is actually waiting, which makes it the narrower check and
    # the one with a person behind it.
    #
    # Input is taken at tool boundaries, so the clock stands still while a tool
    # runs and starts from its return: a 20-minute command holds a message that
    # long without eating into this. This is not a responsiveness target. Ending
    # the turn on this verdict replays the pending message into the next one,
    # so the cost of firing is a restart, not a loss.
    agent_unread_grace_s: float = 1800.0
    # How long a session may keep producing output with no tool call and no
    # ending before it is called stuck. This is the gate for a loop: a session
    # that talks and never acts keeps every other signal healthy: its records
    # keep arriving and its process stays alive.
    # A long foreground command does not trip it, since it emits no output
    # while it runs. Sized for the longest honest stretch of pure writing, a
    # document drafted with no tool call in between.
    agent_no_progress_s: float = 1800.0

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
    # This is the ONE shape a machine is launched in (结论 46): no base URL, the
    # metering proxy on HTTPS_PROXY, a fake ticket. The proxy asks
    # `/llm/admission` per request and sends it to the subscription pool or
    # rewrites it to the gateway. A deployment without a reachable proxy has no
    # second shape to fall back to — it refuses and says so.
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
    # Where the proxy's own CA is mounted from. The sandbox
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
    # CLI, git and model traffic go to it, and the device-flow approval link is
    # built from it. For a NAT'd device this must be publicly reachable
    # (outbound-only for the link WS; the CLI's calls are normal outbound
    # requests). Every machine-facing URL is `{base}/<backend path>`, so the
    # base must map 1:1 onto the backend's ROOT. Behind a reverse proxy that
    # strips an `/api` prefix, that means the base ends in `/api` — otherwise
    # `/topics/...` lands on the SPA, which answers 200/405 and drops the call
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

    # --- MicroCloud: the platform's pool of cloud hosts ---
    # Every cloud host belongs to the platform, under one MicroCloud customer and
    # account of its own, and carries the sandboxes of sessions from any project.
    # Cheese is one MicroCloud tenant and keeps the opaque provider secret
    # server-side. Empty secret = the feature reports itself unavailable without
    # breaking self-hosted compute.
    microcloud_base_url: str = ""
    microcloud_tenant_secret: str = ""
    microcloud_timeout_s: float = 30.0
    # Pin a specific granted offering (machine type + zone + template); 0 = take
    # the first active one, which is right while a tenant is granted exactly one.
    microcloud_offering_id: int = 0
    # The size every cloud host is created with. Every value is clamped into the
    # chosen offering's own range, so these are preferences, not guarantees.
    microcloud_default_cores: int = 2
    microcloud_default_memory_mb: int = 4096
    microcloud_default_disk_gb: int = 20
    # Prepare the default CPU offering; zero disables replenishment.
    microcloud_warm_pool_size: int = Field(default=0, ge=0, le=5)
    # Requires deploy/cloud-control.py on the backend host before enrollment.
    microcloud_direct_control: bool = False
    microcloud_warm_max_age_seconds: int = Field(default=3600, ge=300, le=86400)
    microcloud_login_user: str = "cheese"
    # What one session's sandbox on a Cloud machine may use
    # (`remote_execution/sandbox_host.py`), whatever else shares the machine.
    # Memory: 3 GiB of the default 4 GiB machine. Sessions in this repository's
    # own earlier sandboxes had `pnpm run build` and `vue-tsc` OOM-killed at
    # 2 GiB (docs/topics), and the limit is there so that a session over it is
    # killed alone, not to share the machine out evenly. No swap, so a session
    # at its limit is killed instead of pushing the machine into swap. CPU:
    # the default machine's two cores, a ceiling that only binds on larger
    # machines; below it sessions share by equal weight. Processes: stops a
    # fork bomb, well above the threads a Node or JVM build starts.
    cloud_sandbox_memory_mb: int = Field(default=3072, ge=64)
    cloud_sandbox_swap_mb: int = Field(default=0, ge=0)
    cloud_sandbox_cpus: int = Field(default=2, ge=1, le=1024)
    cloud_sandbox_pids: int = Field(default=4096, ge=16)
    # An operator's SSH public key, authorised on every machine the platform
    # opens, next to the one-shot bootstrap key. That key is erased the moment
    # enrollment succeeds, so without this nobody can read a Cloud machine's
    # connector journal afterwards — which is why the 2026-08-29 failure on
    # machine 477 was never diagnosed. Platform-provisioned machines only: a
    # self-hosted box is someone else's and never gets a key of ours.
    microcloud_operator_ssh_pubkey: str = ""
    # The platform's fund account for its hosts, and the balance kept in it.
    # MicroCloud bills compute against this; 0 disables top-ups (an operator funds
    # it by hand).
    microcloud_account_name: str = "compute"
    microcloud_initial_funds: float = 1000.0
    # Sandbox slots per host core: how many sessions' homes one host carries. A
    # home occupies its slot from placement until its work is pushed away or its
    # room's cleanup removes it, idle or not.
    cloud_host_slots_per_core: int = Field(default=2, ge=1, le=16)
    # Pre-scale: when the free slots of the pool's live hosts fall below this,
    # the pool sweep claims (or creates) the next host before anyone waits on it.
    cloud_pool_min_free_slots: int = Field(default=2, ge=0, le=64)
    # How long a host with no session homes is kept before it is released.
    cloud_host_idle_hold_s: int = Field(default=1800, ge=0, le=86400)
    # The most hosts the pool holds at once, legacy hosts draining excluded. It
    # protects the MicroCloud cluster; a session that finds the pool full is told
    # capacity is tight and to try later.
    cloud_pool_max_hosts: int = Field(default=20, ge=1, le=500)
    # How long a SETTLED machine may go without being re-checked against
    # MicroCloud by the sweep. Never would let a machine destroyed upstream sit
    # here as `running` forever (which happened, and also consumed the
    # per-project limit).
    microcloud_reconcile_interval_s: float = 120.0
    # How often to sweep for machines that came up and still need enrolling as
    # devices. Ten seconds, not sixty: a Cloud topic's first turn waits on this
    # clock, and at 60s a person waited up to a minute on a timer for a machine
    # that was already there. A tick with nothing unsettled is three cheap
    # queries.
    machine_enroll_interval_seconds: int = 10

    # --- Agent sandbox (spec §9.1: 每话题在隔离容器里跑 claude + 原生工具) ---
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
        `secrets.token_hex(24)`, and the cost was not theoretical: a box's
        session token is baked into the environment of the long-running session
        at launch and never refreshed, so a fresh per-process secret invalidated
        every existing box's token the instant the backend restarted. The whole
        deployment went deaf at once — every call from a session 401ing into
        nothing while the agent inside worked perfectly — recovering only by
        destroying each box (and with it the session that IS that topic's
        conversational continuity).

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

    # Project-level concurrency ceiling: at most this many agent turns run at
    # once per project; turns beyond it queue (visible as a system event).
    # Overridable per project via project.settings["max_concurrent_turns"].
    #
    # The number comes from the room side. A room runs up to
    # `MAX_RESIDENT_TASKS_PER_ROOM` threads and its own line is not one of them,
    # so a saturated room is five turns, and a project normally has more than
    # one room working. At 2, a single busy room queued three of its own threads
    # behind itself while the rest of the project waited on top of that.
    #
    # This is the ONLY concurrency gate in the system: nothing limits how many
    # turns land on ONE machine. So this number also decides what a single
    # self-hosted laptop can be asked to run at once, which is not what it is
    # named for and not a limit anybody chose. Raise this and that exposure
    # rises with it, until a per-machine gate exists.
    max_concurrent_turns: int = 16

    # Snapshotted into each archival operation, never restarted by deployment.
    topic_archive_cleanup_delay_s: int = Field(default=300, ge=0)
    # Seconds between orphan sweeps (AgentWorkRunner.sweep_orphans). On by
    # default: it consumes no model calls unless it actually finds a killed
    # turn, and its whole purpose is catching the case where nothing else will
    # ever look — a turn dying without the process dying.
    orphan_sweep_interval_s: int = 300
    # How long a backend on its way out waits for the prompts it is still
    # sending, and the receipts it is still expecting, before it hands its
    # sessions to the next backend anyway (`app.core.ownership`). It has to fit
    # inside the container's `stop_grace_period` together with uvicorn's own
    # graceful shutdown, or the handover is cut off by a SIGKILL halfway.
    handover_timeout_s: float = Field(default=20.0, ge=0)
    chat_progress_check_interval_s: int = 15
    chat_progress_reminder_after_s: int = Field(default=600, gt=0)
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

    # --- GitHub App (cheesex-app, #188 minimal / #192 git integration) ---
    # The platform's GitHub credential: the backend holds the App private key
    # and mints short-lived installation tokens from it. Unset = the
    # /sandbox/forge-token endpoint cannot issue GitHub credentials.
    github_app_id: int | None = None
    github_app_private_key_path: str | None = None
    # Which installation to mint a token for is resolved per-project via the
    # project_git_installations table (#192), not a config value — a
    # deployment can have many connected repos, each with its own
    # installation_id.
    github_app_slug: str = "cheesex-app"

    forgejo_url: str = ""
    forgejo_api_url: str = ""
    forgejo_admin_token: str = ""
    forge_attribution_default: bool = True
    forge_event_relay_url: str = ""
    forge_event_secret: str = ""
    # Only the public relay loads the deployment -> shared secret mapping.
    forge_event_relay_keys: dict[str, str] = {}
    forge_event_github_secret: str = ""
    forge_event_github_app_id: int | None = None
    forge_event_github_public_key: str = ""
    # GitHub App installation ID -> deployments authorized for that installation.
    forge_event_github_installations: dict[str, list[str]] = {}
    forge_webhook_url: str = ""

    # --- 闸门孤儿卡扫底 (2026-08-11) ---
    # How often to look for `pending_gate` cards nobody will ever settle (the
    # gate runner is an in-memory asyncio task — see review/gate_sweep.py for
    # the three ways it goes missing). Startup does one sweep unconditionally;
    # this interval is what covers the "process still alive, task died" half.
    # 0 disables the periodic sweep (the startup one still runs).
    gate_sweep_interval_s: int = 300

    # --- 记忆整理 dream (2026-09-27) ---
    # 多久问一次「有没有项目该整理记忆了」。这一档**不是**整理的周期：该不该跑由
    # 项目自己的花销和上次整理的时刻决定（`domain/memory/dream.py`），这里只是那
    # 台钟走多快。所以它可以跑得勤（问一次很便宜，不该整理的项目问完就返回），而
    # 真正跑起来的整理是几分钟一轮的会话。0 关掉这个 job。
    memory_dream_sweep_interval_s: int = 600

    # --- 旧表迁移 (2026-09-27) ---
    # dry-run 出来的报告要**人**点头才落笔（`domain/memory/migration.py`），而点头
    # 的那个人是固定的一个：写在这里而不是每次调用带一个参数——「谁复核」是这次
    # 迁移的决定，不是请求的属性，跟着请求走就等于谁都能给自己批。
    memory_migration_reviewer: str = "wangchangxin"
    # 问模型那一步的超时。一次问的是一批旧记忆（几十条、几万字），比一次普通对话
    # 长得多，默认的几十秒会在长项目上直接超时。
    memory_migration_timeout_s: float = 600.0
    # The gateway model that sorts old memories, and what its key may spend per
    # 30 days: the platform runs it, so no person's credits pay for it.
    memory_migration_model: str = "deepseek-flash"
    memory_migration_budget_usd: float = 10.0
    # 一次问模型的旧记忆条数（见 `migration.MIGRATION_CHUNK`）。
    memory_migration_chunk: int = 60

    # --- 两阶段采纳 (PR迭代式, 2026-08-09) ---
    # How often the background poller checks an open PR's CI / the deploy
    # workflow it triggers after merge.
    accept_pr_poll_interval_s: int = 300
    # 卡面上的合并态是快照，而采纳按钮按它亮不亮。读卡这条路也会重算一次陈旧的
    # 快照 (`AcceptService.refresh_stale_pr_snapshots`)，否则界面每 15s 来读一
    # 次、读到的却是同一份旧快照，得等满一个轮询周期才看见 CI 绿了。这个地板是
    # 必须的：读卡是热点，不能每个读者都替全平台去问一次 GitHub。
    accept_pr_snapshot_floor_s: int = 60
    # 后端报错回房间 (issue #283): how often to close expired burst windows so a
    # flood that STOPPED still reports how big it was. Only bounds how late that
    # summary line is — the dedup window decides whether it exists. 0 disables.
    backend_error_flush_interval_s: int = 60
    # --- notifications and deadlines ---
    # Two jobs nothing in a request path can do. An undrained email queue is an
    # inbox that never receives; an unswept deadline is a promise the platform
    # made and quietly did not keep. Each failure is silent, which is why the
    # intervals are on by default. 0 disables one.
    notification_email_drain_interval_s: int = 60
    #: 推送比邮件跑得勤：推送的全部价值在于它比人自己回来看更早，一分钟的排队等待
    #: 已经吃掉不少。邮件反过来 —— #1084 要它比推送晚一档。
    notification_push_drain_interval_s: int = 15
    #: 投递账本的补发。「写入之后、发出之前崩掉」那一档没有别的出路：那一行已经和
    #: 事件一起提交了，发送这一半没人再碰它。不跑就是一份丢失记录，不是一次补救。
    delivery_resend_interval_s: int = 60
    task_deadline_sweep_interval_s: int = 900
    # merge_method for the auto-merge (GitHub: merge | squash | rebase). MUST
    # be one the target repo actually allows — GitHub answers 405 forever for
    # a disabled one, which is exactly how 两阶段采纳 shipped never having
    # merged once (hardcoded "merge" against a squash-only repo). Configurable
    # rather than hardcoded so a differently-configured repo isn't a code
    # change; deliberately NOT auto-retried with another method, since 405 also
    # means draft PR / branch protection and silently switching would both mask
    # those and produce merge commits in repos that allow several methods.
    accept_pr_merge_method: str = "squash"

    # --- the architecture ratchet (后台「棘轮」页) ---
    # The repository whose snapshots the page shows. The platform's own: the
    # ratchet answers 「平台自己的债还得怎么样」, and `arch-metrics.yml` collects
    # there. Configurable so a fork or a second deployment is not a code change.
    ratchet_repository: str = "SageSeekerSociety/cheese"
    #: How often the backend pulls new `ratchet-snapshot` artifacts. The page
    #: reads the table and never GitHub, so this clock is what makes a merge
    #: visible there; 0 disables the pull and leaves the table as it stands.
    #: 15 minutes is the design's interval — one listing call per tick, and a
    #: merge does not need to appear faster than a person can read the page.
    ratchet_ingest_interval_s: int = 900
    #: How many points the page draws. A bound on one response, and the window
    #: the direction is computed over; the archive keeps everything.
    ratchet_series_points: int = 60

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
    # The private bucket, for task snapshot bundles (room_task/snapshots.py);
    # the public upload bucket is never used for them. The name is the bucket's
    # first use: it also holds the transcript objects `raw_transcripts` indexes.
    transcript_s3_bucket: str = ""
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    s3_public_url: str | None = None

    # --- Human auth (P1 agent-as-user / 真鉴权) ---
    # Enforce topic access (成员/角色/项目 checks). Ops kill-switch: set false to
    # disable the membership check entirely if a token rollout surfaces an
    # unexpected block. It never lets a request body name its author.
    authz_enforce_topic_access: bool = True

    # --- 主仓产品配置并入 (fusion merge, restored): main's live product domains
    # (PDF task drafts, rank checks, email/notifications, real-name
    # encryption) read these off settings. The merge dropped them, so those code
    # paths hit AttributeError at runtime; restored verbatim from origin/main
    # (aliases kept where the env var name differs from the field name). ---
    # The gateway model that turns an uploaded PDF into task drafts; each page
    # is one call, charged to the publisher's personal credits.
    task_draft_model: str = "deepseek-flash"
    task_draft_max_tokens: int = 4096
    task_draft_timeout_s: float = 300.0
    pdf_import_max_pages: int = Field(default=20, ge=1, alias="PDF_IMPORT_MAX_PAGES")
    pdf_import_max_concurrency: int = Field(
        default=3, ge=1, le=10, alias="PDF_IMPORT_MAX_CONCURRENCY"
    )
    # The draft models whose deployment accepts image content blocks. A PDF page
    # with no text layer (a scan) is rendered to a PNG and sent to the model only
    # when `task_draft_model` is named here. These models are text models by
    # default, and an image sent to one that cannot read it is a wasted, billed
    # call — so the fallback is opt-in per model, not inferred. Deployment env
    # name TASK_DRAFT_VISION_MODELS carries a JSON array (pydantic-settings
    # parsing for a set/list field).
    task_draft_vision_models: set[str] = {"deepseek-flash"}

    email_from_address: str = Field(default="", alias="EMAIL_FROM_ADDRESS")
    email_smtp_host: str = Field(default="", alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=587, alias="EMAIL_SMTP_PORT")
    email_smtp_username: str = Field(default="", alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    email_smtp_ssl: bool = Field(default=False, alias="EMAIL_SMTP_SSL_ENABLE")
    #: A second SMTP account tried when the one above fails to send. Unset
    #: (empty host) means there is no fallback and a failure is final.
    email_fallback_from_address: str = Field(
        default="", alias="EMAIL_FALLBACK_FROM_ADDRESS"
    )
    email_fallback_smtp_host: str = Field(default="", alias="EMAIL_FALLBACK_SMTP_HOST")
    email_fallback_smtp_port: int = Field(default=587, alias="EMAIL_FALLBACK_SMTP_PORT")
    email_fallback_smtp_username: str = Field(
        default="", alias="EMAIL_FALLBACK_SMTP_USERNAME"
    )
    email_fallback_smtp_password: str = Field(
        default="", alias="EMAIL_FALLBACK_SMTP_PASSWORD"
    )
    email_fallback_smtp_ssl: bool = Field(
        default=False, alias="EMAIL_FALLBACK_SMTP_SSL_ENABLE"
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

    # --- 浏览器推送（#1084 第 5 步）---
    #
    # 一对 VAPID（Voluntary Application Server Identification）密钥：推送服务商
    # （Chrome 的 FCM、Firefox 的 autopush）用公钥认出推送是谁发的，浏览器订阅时
    # 也要拿着同一个公钥。没有配就整条链路关掉 —— 不是报错，是这个部署没开这个
    # 渠道，订阅接口会如实说不可用。
    #
    # 私钥不能进仓库也不能进镜像：它是「以这个站的身份发推送」的凭据。
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    #: VAPID 要求一个联系方式，推送服务商用它在出问题时找到运营者。
    vapid_subject: str = Field(default="mailto:ops@okcheese.com", alias="VAPID_SUBJECT")
    notification_push_queue_key: str = Field(
        default="cheese:notifications:push", alias="NOTIFICATION_PUSH_QUEUE_KEY"
    )
    notification_push_batch_size: int = Field(
        default=100, alias="NOTIFICATION_PUSH_BATCH_SIZE"
    )
    notification_push_max_retries: int = Field(
        default=3, alias="NOTIFICATION_PUSH_MAX_RETRIES"
    )

    @property
    def web_push_configured(self) -> bool:
        """这个部署能不能发浏览器推送。

        两把钥匙缺一个就不能：只有公钥订阅得成但发不出去，只有私钥连订阅都换不到
        凭据。所以这两个字段一起判断，调用点不各自数一遍。
        """
        return bool(self.vapid_public_key and self.vapid_private_key)

    enforce_task_participant_limit_check: bool = Field(
        default=False, alias="APPLICATION_ENFORCE_TASK_PARTICIPANT_LIMIT_CHECK"
    )
    rank_check_enforced: bool = Field(
        default=False, alias="APPLICATION_RANK_CHECK_ENFORCED"
    )
    rank_jump: int = Field(default=1, alias="APPLICATION_RANK_JUMP")
    # The master key for every value the app encrypts at rest (#1482): base64url
    # of 32 random bytes. A comma-separated list rotates it — the first entry
    # encrypts, every entry decrypts. Empty on a developer's machine and in the
    # test suite, which then use DEVELOPMENT_DATA_ENCRYPTION_KEY; a deployment
    # refuses to boot without a real one (`_require_data_encryption_key`).
    # Read through `app.core.crypto`, never directly.
    data_encryption_key: str = ""

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
          draws (secure cookies). Still checked, for any deployment that does
          not run through this compose file.

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

    @model_validator(mode="after")
    def _require_platform_admins_on_deployment(self) -> "Settings":
        """Fail the boot when a deployment has nobody who can run the admin screens.

        ``platform_admin_handles`` is the **root** admin list, and it is still
        the only way in: no role behind it, no default member set, nothing in the
        UI that promotes you. A deployment can now add admins from the page
        (`platform_admins`, the 成员管理 block) — but **adding one is itself an
        admin action**, so an empty root list is not "we have not got round to
        appointing an admin yet". It is a deployment where nobody can ever
        appoint one:

        - 成员管理 — the screen whose whole job is adding the next admin — is
          admin-only, so there is no way back in from the product at all.
          This is why the root list is the half the page cannot delete: one
          mis-click on a list that was the only copy would lock everyone out
          permanently.

        Nothing in the running system can detect that state from the inside,
        which is why it is checked at boot (#338/#439's rule: an unset
        credential that degrades silently becomes a loud, boot-time event).

        Same two-signal test as the JWT guard above, for the same #342 reason —
        ``deployed_via_compose`` is authority because it cannot fall back, and
        ``environment`` covers deployments that do not run through that compose
        file. Local dev and the test suite trip neither, so they keep the empty
        default and the suite still runs with no config.

        RuntimeError rather than ValueError, so the message is not wrapped by
        pydantic's ValidationError repr (which dumps the whole input dict —
        including every secret in it) — same reason as the guard above.
        """
        # An entry that is empty or whitespace is worse than a missing entry:
        # the list is non-empty, so this guard passes, and the handle it names is
        # one that no account can ever authenticate as.
        blank = [h for h in self.platform_admin_handles if not h.strip()]
        if blank:
            raise RuntimeError(
                "PLATFORM_ADMIN_HANDLES contains an empty entry. Handles are "
                "matched against the account's handle exactly, so an empty "
                "string can never match anyone — this is a list with a typo in "
                "it, not a list with an admin in it. Set it to a JSON list of "
                'handles, e.g. PLATFORM_ADMIN_HANDLES=\'["alice","bob"]\'.'
            )

        if self.platform_admin_handles:
            return self

        if not self.deployed_via_compose and self.environment in (
            "development",
            "test",
        ):
            return self

        raise RuntimeError(
            "PLATFORM_ADMIN_HANDLES is empty on a deployment ("
            f"ENVIRONMENT reads '{self.environment}'"
            + (
                ", started by the deploy compose file"
                if self.deployed_via_compose
                else ""
            )
            + "). This is the only way into the admin screens — the page "
            "itself can add admins, but only an admin can do that, so an empty "
            "list here is a deployment nobody can get into. Set it to the "
            "handles that should run the platform (they are the ones the page "
            "cannot remove), as a JSON list: "
            'PLATFORM_ADMIN_HANDLES=\'["alice","bob"]\' (see '
            "deploy/.env.prod.example). If you are sure nobody should, set it "
            "to a handle you control rather than leaving it empty."
        )

    @model_validator(mode="after")
    def _require_data_encryption_key(self) -> "Settings":
        """Refuse to boot on a malformed key anywhere, or a missing one deployed.

        Every secret the app keeps at rest — 2FA secrets, forge passwords,
        OAuth tokens, real-name fields — is encrypted under this key, so a
        deployment running on the public development key protects nothing, and
        one whose key silently changed could read none of it back. Same two
        signals as the JWT_SECRET guard, for the same reason (#439), and a
        RuntimeError for the same reason: a ValueError would be wrapped into a
        ValidationError whose repr carries every secret in the input.
        """
        generate = (
            'python -c "import base64, os; '
            'print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
        try:
            keys = parse_data_encryption_keys(self.data_encryption_key)
        except ValueError as exc:
            raise RuntimeError(f"{exc} Generate one with: {generate}") from None
        development = base64.urlsafe_b64decode(DEVELOPMENT_DATA_ENCRYPTION_KEY)
        if keys and development not in keys:
            return self
        if not self.deployed_via_compose and self.environment in (
            "development",
            "test",
        ):
            return self
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY is missing or is the public development key on "
            f"a deployment (ENVIRONMENT reads '{self.environment}'"
            + (
                ", started by the deploy compose file"
                if self.deployed_via_compose
                else ""
            )
            + "). It encrypts every secret stored in the database; set it in "
            "the backend env file and keep it with the database backups, "
            f"because nothing encrypted under it can be read without it. "
            f"Generate one with: {generate}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
