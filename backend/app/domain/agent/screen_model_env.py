"""The model environment a Claude Code screen is launched with.

Every request a session the project pays for makes runs through the metering
proxy, so the screen is handed the proxy's CA, a CONNECT credential and the
tunnel to reach it. A member's own Claude Code (#2991) is handed none of it: it
signs in with the login its owner gave the platform on that machine.
"""

import time
from pathlib import Path

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.core.sentences import say
from app.domain.agent import provider_env
from app.domain.agent.harness.channel import SESSION_TOKEN_TTL_S, ScreenSetupError
from app.domain.agent.machine_address import tunnel_url as machine_tunnel_url
from app.domain.agent.machine_address import ws_url

# Where the launch script writes the metering proxy's CA on the device (under the
# screen's ISOLATED home) and exports NODE_EXTRA_CA_CERTS to point. The env value
# built here carries the literal placeholder; only the script knows the real home.
# The `.claude` in it is the launcher's config dir, so this string and
# `device_launch` have to agree — it is written down twice today, once on each
# side of the seam.
_DEVICE_PROXY_CA_PATH = "$HOME/.claude/proxy-ca.pem"


def _read_proxy_ca() -> str:
    """The metering proxy's CA, read where THIS backend can see it — required for
    every device turn (the launcher embeds it; without it the screen's `claude`
    cannot trust the proxy and fails as an opaque TLS error). Raising here, with
    the setting named, is the only exit: there is no second launch shape to fall
    back to, and falling back to one would swap the model out from under the
    user — the exact failure #325 G2 removes."""
    path = settings.subscription_ca_backend_path.strip()
    if not path:
        raise ScreenSetupError(say("screenBillingCaPathUnset"))
    try:
        ca = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ScreenSetupError(
            say("screenBillingCaUnreadable", path=path, error=str(exc))
        ) from exc
    if not ca.strip():
        raise ScreenSetupError(say("screenBillingCaEmpty", path=path))
    return ca


def _credential_expiry(token: str) -> int:
    """Read the credential's birth expiry, retained with its device session.

    A development token without an expiry gets the normal session lifetime.
    """
    claims = scoped_token_claims(token)
    exp = claims.get("exp") if claims else None
    if isinstance(exp, int):
        return exp
    return int(time.time()) + SESSION_TOKEN_TTL_S


def screen_model_env(
    env: dict | None,
    *,
    token: str,
    project_id,
    topic_id,
    resource_id,
    agent_handle: str,
    api_base: str,
    no_proxy: str,
) -> tuple[dict, str, int]:
    """The screen's model environment, the CA its launch writes, and when its
    credential expires."""
    # 一台机器只有一种启动环境（结论 46）。Every request from every machine
    # reached this way runs through the metering proxy — an enrolled device
    # and a leased Cloud box alike, since both are launched from right here
    # (#325 G2) — and the proxy asks `/llm/admission` per request whether it
    # goes to the subscription pool or is rewritten to the gateway. There is
    # no second shape and no fallback: falling back silently is exactly the
    # model swap this kills (dev shipped device screens with
    # CLAUDE_MODEL=deepseek-chat while users thought they were talking to
    # Claude). This is what ``builds_model_env`` declares.
    #
    # A member's own Claude Code on their own machine (#2991) signs in with
    # the login its owner gave the platform there, and none of its requests
    # reach the metering proxy: no CA, no CONNECT credential, no tunnel.
    own_login = (env or {}).get("CHEESE_OWN_LOGIN") == "1"
    if own_login:
        ca_pem = ""
        model_env = {**(env or {})}
        for key in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_MODEL",
        ):
            model_env.pop(key, None)
        credential_expires = _credential_expiry(token)
        model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        model_env["CHEESE_PREVIEW_URL"] = ws_url(api_base, "/preview/tunnel")
    else:
        # The Claude login is the host's own, established by the launch script;
        # the scoped cheese token below only proves which project to bill.
        ca_pem = _read_proxy_ca()
        # Session-length TTL, not the 1h default. This token is baked into the
        # bare process's HTTPS_PROXY (CONNECT credential), read ONCE at process
        # start and never hot-refreshed; the screen is reused across turns (a
        # reassert only re-attaches, it does not relaunch claude). A 1h token
        # thus expires under a still-running process, and every turn after the
        # first hour is rejected by the metering proxy (407) — the agent looks
        # dead. Same session lifetime as the CHEESE_TOKEN minted alongside it.
        session_token = mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
            resource_id=str(resource_id),
            # WHO acts with it — a room cannot answer that once it may seat
            # more than one agent, so the launcher, which knows, says it.
            agent_handle=agent_handle,
        )
        sub = provider_env.subscription_provider(
            ca_path=_DEVICE_PROXY_CA_PATH,
            project_id=str(project_id),
            topic_id=str(topic_id),
            no_proxy=no_proxy,
        )
        # Which model a request runs on is decided at one control point —
        # admission, when the request reaches the metering proxy (结论 46) — and
        # the proxy writes the answer into the REQUEST BODY on its way out; that
        # always wins. The caller's ``ANTHROPIC_MODEL`` (chat `_model_kwargs`)
        # names the same binding at launch only so Claude Code builds the system
        # prompt for that model, and a change of binding reopens the screen at
        # the next task boundary. The three family aliases the CLI addresses
        # subagents by stay out: the subagent default is its own binding.
        model_env = {**(env or {})}
        # Dropped, not overridden: `subscription_provider` only ADDS keys, and
        # any of these surviving from a caller's env flips the CLI out of
        # subscription mode or asks a pool for a model nobody bound.
        for key in (
            "ANTHROPIC_BASE_URL",
            "CLAUDE_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
        ):
            model_env.pop(key, None)
        model_env.update(sub.env)
        # The tunnel's CONNECT credential: the helper reads it from a file the
        # launch script writes, and it carries the session's place claims.
        model_env["CHEESE_CONNECT_TOKEN"] = session_token
        # Read by Claude Code only when its login comes from the environment (a
        # host with a setup-token): the scopes it then believes it holds. RC is
        # served by Cheese, not Anthropic, so the sessions scope enables it
        # whatever the token grants upstream. A stored login has its own.
        model_env["CLAUDE_CODE_OAUTH_SCOPES"] = (
            "user:inference user:profile user:sessions:claude_code"
        )
        # The meter accepts model hosts, not package registries.
        model_env["CHEESE_MODEL_PROXY"] = "1"
        # Read by the launch script: it writes the helper and the token file,
        # starts the helper before `claude`, and exports the port the helper
        # bound as HTTPS_PROXY. Carried on the env rather than as arguments
        # because a remote machine's launch is built entirely from `extra_env` —
        # there is no other channel into that builder.
        model_env["CHEESE_TUNNEL_URL"] = machine_tunnel_url(api_base)
        # Preserve the birth expiry across device and backend restarts.
        credential_expires = _credential_expiry(session_token)
        model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        # 运行环境预览's dial-out address. Derived from the base this machine
        # already reaches for git and the CLI rather than configured separately:
        # the preview rides the path the connector proved, so a deployment that
        # can host a device can host a preview with nothing further to set.
        model_env["CHEESE_PREVIEW_URL"] = ws_url(api_base, "/preview/tunnel")
    return model_env, ca_pem, credential_expires
