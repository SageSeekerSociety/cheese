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
    agent_model: str = "glm-4.6"
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    # Model aliases the CLI may resolve internally; map them to the provider.
    agent_haiku_model: str | None = "glm-4.5-air"
    agent_sonnet_model: str | None = "glm-4.6"
    agent_opus_model: str | None = "glm-4.6"
    agent_system_prompt: str = (
        "你是「芝士」，知是平台里的 AI 队友。你贯穿一个项目的全过程，"
        "了解项目的话题、决策和进展。回答要说人话，让零基础的同学也能看懂，"
        "少说废话。当你引用项目记忆里的事实时，自然地点明依据。"
    )
    # Working directory for the agent's git-backed workspace (one repo per project).
    workspace_root: str = "./.workspaces"

    # --- Agent sandbox (spec §9.1: 每话题在隔离容器里跑 claude + 原生工具) ---
    # When on, the interactive turn runs `claude` INSIDE a per-topic Docker
    # container (native Bash/Read/Write jailed there) via the cli_path shim, and
    # platform actions go through the `cheese` CLI → REST. Requires Docker.
    agent_sandbox_enabled: bool = False
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

    # --- Scheduler (spec §9.1: 确定性调度——定时巡检/生命周期) ---
    # Seconds between automatic 定期巡检 ticks across all projects. 0 = off
    # (manual heartbeat only; default off so dev/tests don't burn model calls).
    scheduler_interval_seconds: int = 0

    # --- App ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
