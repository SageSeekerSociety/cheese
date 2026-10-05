# Subscription metering proxy

The metering proxy between a session's Claude Code and Anthropic. It meters
every `/v1/messages` response into
`usage.jsonl` (ingested by the backend — `subscription_ingest`), and refuses a
turn its project cannot afford (backend `/llm/admission`, plus a rolling token
cap as backstop). Design context: issue #218 and
`docs/plans/2026-08-10-usage-unification-design.md`.

## Files

- `billing_addon.py` — the mitmproxy addon (glue only).
- `cheese_billing_core.py` — token verify / metering / admission logic, stdlib
  only, unit-tested from `backend/tests/unit/test_metering_proxy_core.py`.
- `compose.yml` — the container. Values via a box-local `.env` (see header).

Gateway responses emit `gateway_request_timing` in the container log. Each record
contains the HTTP request and response timestamps, the end of route selection,
admission-check duration, and `x-litellm-call-id` for correlation with gateway
spend logs. Bodies, credentials, and arbitrary headers are excluded. Failed
upstream requests retain their available timestamps. These intervals include
proxy and gateway work; they are not a measurement of provider processing alone.

## One interception point, two destinations (#243)

Every sandbox's traffic passes through here, and the destination is a per-request
decision rather than something pinned into the sandbox's launch environment:

- `subscription` → forward to Anthropic with the platform's Claude credential
  in place of the session's placeholder.
- `gateway` → rewrite to `CHEESE_GATEWAY_BASE` (LiteLLM) with the project's
  virtual key; the platform's Claude credential never reaches the gateway.

The backend decides (`POST /llm/admission` answers both "may it run" and "where
does it go"); this proxy only carries it out. That is why a project can change
supply without restarting its agent. The proxy asks with the session's scoped
token, which names the room, and with its own credential (`CHEESE_SCOPED_SECRET`
as `X-Cheese-Token`): the backend hands a gateway project's key only to a caller
presenting that credential, since the session holds the same scoped token. Fail-open has a direction: an unreachable
control plane falls back to the subscription — the destination this proxy has
always had — never to a gateway whose per-project key it would not hold.

## Who reaches which listener (and the MicroCloud gap this closed)

Two listeners, because a client's *shape* decides how it can be steered here at
all:

| Listener | Steered by | Who |
|---|---|---|
| `:443` reverse | DNS (`--add-host`) | containers on this box — needs root to write hosts |
| `:8444` CONNECT | `HTTPS_PROXY` | every bare process: local device screens, **and MicroCloud machines** |

A remote machine has no root, no docker and no `/etc/hosts` to rewrite, so
CONNECT is its only route. It also cannot set `ANTHROPIC_BASE_URL` instead:
that flips Claude Code into API-key mode, where it ignores the OAuth token
entirely — so a subscription turn *must* be steered at the transport layer.

`:8444` used to be pinned to the docker bridge, which is what actually blocked
remote subscription turns. It was never a routing problem: measured 2026-08-14
from machine `192.168.31.2`, the box answers on `192.168.16.5:22` while
`172.17.0.1:8444` does not — the machine shares the box's `/20`, but nothing
routes to a bridge address. Hence `CONNECT_BIND_HOST` (see `compose.yml`);
`0.0.0.0` on a box that serves machines, since local screens still use the
bridge address.

Both halves are needed: this publishes the listener, and the backend's
`subscription_device_proxy_host` is the address a machine is *told* to use.
Set one without the other and every launch logs an error naming the missing one.

## Why the constraints are what they are

- **Transparent**: a subscription's legitimacy rests on the client being Claude
  Code itself. The addon may replace the auth header on the way to the gateway
  and drop/refuse connections;
  it must never rewrite a request body — the fingerprint has to stay Claude
  Code's own, and the account at risk is a person's.
- **Attribution from claims, not headers** (#198): with `CHEESE_SCOPED_SECRET`
  set, project/topic come from the verified scoped token the session proves as
  its CONNECT password. `CHEESE_ALLOW_HEADER_ATTR=1` re-enables the legacy spoofable
  `x-cheese-attr` header and is acceptable ONLY while the proxy is reachable
  solely on the box's own docker bridge.
- **The CONNECT gate fails closed**: no `CHEESE_SCOPED_SECRET` means every
  CONNECT is refused (407), not relayed on trust. Now that the bind address is a
  per-box setting, the dangerous configuration — widened bind, secret forgotten
  — has nothing about it that looks like a failure, so it must not be one that
  quietly works. `CHEESE_ALLOW_HEADER_ATTR=1` stays the one explicit opt-out.
- **Fail-open admission**: an unreachable backend logs and allows. A brake that
  can take the platform down is worse than the overspend it prevents; the token
  cap stays as the deployment-wide backstop. A session the backend answered for
  within the last hour keeps that answer's pool, key and model while it is
  unreachable; only a session with no answer on record goes to the subscription.

## Release on the dev box

The dev application deployment releases the metering image after updating the
application, using the same full commit SHA and requiring its image build and
Required CI to succeed. A healthy proxy already running that digest with the
same configuration is left running, so a release that changes nothing does not
interrupt active streams.

The configuration is the compose file being released and the box-local `.env`.
Each release labels the container it creates with a SHA-256 of the two
(`cheese.metering-proxy.config-sha256`), and the next release exits early only
when the image, health and that hash all match. A change to only the `.env` or
the compose file is therefore applied by the next release, which recreates the
proxy. Only the hash is stored; the `.env` itself is never printed or copied.
A container created any other way, or one restored by a rollback, has no hash
and is recreated by the next release.

Use the **Release metering proxy** GitHub Actions workflow from `main`, with the
full merged commit SHA. Its image build and Required CI must both have succeeded
for that exact SHA. Acknowledge the interruption only when ready to recreate the
proxy: active model streams can disconnect.

The build pins mitmproxy 12.1.2 and packages the addon, billing core and control
answers into the image. The release pulls the full-SHA tag, resolves its registry
digest and starts that digest. No host source directory is mounted over `/addons`.

The existing box-local `.env` remains at
`~/cheese-proxy-new/deploy/metering-proxy/.env`. The release preserves its project
directory, published ports, ledger and CA mounts.
`CHEESE_ADMISSION_URL` must point at the backend's `/llm/admission`;
`USAGE_LOG_DIR` must match the backend's `SUBSCRIPTION_USAGE_LOG` directory.
`CERTS_DIR` holds the existing CA.
Do not print the environment or replace the CA during a release.

Before recreating the service, the release saves its image ID and raw compose
files in a private `releases/<sha>-<run>-<attempt>/` directory. It leaves legacy
source files in place for the first release's rollback. Failed health checks
restore the previous image and mounts. Health checks require both listeners and
an unauthenticated CONNECT response of 407; they do not call a model provider.

After release, verify a sandbox turn, a usage row attributed to that turn, and a
budget refusal for a test project whose compute grant is exhausted. Listener health alone does not
verify that the platform's credential reaches Anthropic, backend admission or
ledger ingestion.

## The Claude credential

The proxy holds the platform's Claude credentials, and no session holds any: every
Claude Code session boots on `NO_LOGIN_PLACEHOLDER` (`cheese_billing_core.py`),
which authenticates nothing. On a request bound for Anthropic the proxy puts
the selected account's credential in place of the placeholder; on one admission routes to the
gateway it puts the project's virtual key.

The primary account stays in `claude-credential/credential`. Add another account
with `claude-login.sh --account NAME setup-token`; it is stored in
`claude-credential/accounts/NAME/credential`. Set its egress with
`claude-login.sh --account NAME egress set URL` before installing its credential.
Each account needs its own subscription allowance; two tokens for the same
account share that account's limits.

New conversations are distributed across available accounts. Each conversation
keeps its account until that account returns HTTP 429. The proxy records a
persistent cooldown and retries the rejected request once on another available
account, using the same model and request body. Subsequent requests stay on that
account. Existing healthy conversations do not move when another account recovers.

Cooldowns honor `Retry-After` and the binding
`anthropic-ratelimit-unified-reset` header. A quota error without a reset time
disables the account until an operator runs
`claude-login.sh --account NAME cooldown-reset`. Other 429s start with a
five-minute cooldown and back off to one hour after repeated failures. After
cooldown, one new conversation tests the account; concurrent requests continue
using healthy accounts. A failed recovery request extends cooldown. State is
stored in `cooldowns.json` beside the primary credential and survives restarts.

Only complete request bodies up to 64 MiB can be replayed. They are spooled with
at most 1 MiB in memory per request. Early upload rejections and larger requests
retain the original 429, while still cooling the account. Once response streaming
starts, the proxy never replays a request. If every account is cooling, it returns
429 without contacting the provider.

The replay adapter uses the image's pinned mitmproxy 12.1.2 transport. CI runs
real HTTP/1.1 and HTTP/2 proxy tests against a local fake upstream before release.

Credential files are re-read on each request. Use
`<proxy home>/claude-login.sh` on the box, as the directory's owner (every release installs it there):

- `claude-login.sh setup-token` stores a one-year token from
  `claude setup-token`. Nothing renews it; replace it within the year.
- `claude-login.sh login` signs in in the browser, in a throwaway config
  directory, and moves the resulting pair here. The proxy renews the access
  token itself, the way Claude Code does, and writes the new pair back; it is
  the pair's only holder, so no rotation strands anyone. The refresh token's
  own deadline, about 30 days from login, does not move: log in again before
  it (`claude-login.sh status` shows when; the proxy also logs a warning in the
  last three days).
- `claude-login.sh logout` removes it.

The credential can have an egress: an HTTP proxy that every request carrying
it, token refreshes included, leaves through. Nothing else changes route — the
gateway, the answers given here and everything tunnelled raw keep their own.
An egress that is down or refuses the proxy fails the request; it is never sent
direct instead.

- `claude-login.sh egress set http://[user:pass@]host:port` sets it, from the
  next request.
- `claude-login.sh egress test [n]` times n TLS handshakes with Anthropic
  through it and n direct, and prints both.
- `claude-login.sh egress clear` sends the requests direct again.

With no credential, projects on the API-key pool still run. The boot calls only
a real account can answer (`NO_LOGIN_ANSWERS`) are answered here. A request
admission places on the subscription is refused with a 400 naming the missing
login, which the client does not retry; one admission could not place gets a
503 and is retried.

## The ChatGPT accounts

The proxy also holds the platform's ChatGPT subscription accounts, one per
name, each in `<proxy home>/chatgpt-credential/<name>/credential` (JSON:
`access_token`, `refresh_token`, `id_token`, `expires_at` in epoch seconds,
`account_id`). As with Claude it is each pair's only holder: it renews the
access token within ten minutes of expiry and writes the new pair back, and a
refresh token OpenAI calls expired, reused or invalidated (or a 401/403) stops
that account until someone logs it in again. Use `<proxy home>/chatgpt-login.sh`
(every release installs it there):

- `chatgpt-login.sh login <name>` runs OpenAI's device login: it prints a code
  to enter at `https://auth.openai.com/codex/device`, then stores the pair.
- `chatgpt-login.sh import <name> <file>` stores a pair held somewhere else,
  as the JSON above; without `expires_at` it takes the access token's `exp`.
  Stop using the copy it came from: the next refresh rotates the pair.
- `chatgpt-login.sh status [name]`, `ls`, and `logout <name>`.
- `chatgpt-login.sh egress set|clear|test <name> …` gives one account an HTTP
  egress, exactly like the Claude one; its login, refreshes and requests all
  leave through it. Two accounts may share an egress only with the same login
  on it: mitmproxy keeps one tunnel per proxy address, so the proxy refuses
  their requests rather than let one ride the other's tunnel.

The gateway reaches the accounts on a third listener, `:8445`
(`--mode reverse:https://chatgpt.com@8445`). It is published nowhere: the
proxy and the gateway's LiteLLM share an internal docker network,
`cheese-meter-gateway`, on which the proxy is `metering-proxy`, and no sandbox
is on it. A gateway deployment for an account has `api_base`
`http://metering-proxy:8445/chatgpt/<name>` and `api_key`
`CHEESE_CHATGPT_KEY`; the backend accepts that one plain-http base and no
other. Both stacks name the network external and each release creates it when
it is missing, so either may be released first. A proxy brought up by hand
before either release has run fails to start until
`docker network create --internal cheese-meter-gateway` has been run.
The proxy forwards `…/responses[/…]` and `…/models` to
`https://chatgpt.com/backend-api/codex/…`, with the account's token,
`ChatGPT-Account-Id`, `originator: cheese` and `version: <client version>`
(below); anything else, an unknown account, or one with no usable login is
answered with an error and not forwarded. A separate
listener, so that no path on the Anthropic ones can reach an account; the key
stays as a second line of defence behind the network. These responses are not
metered here: the gateway records their spend.

ChatGPT decides which models an account is offered from the Codex client
version it is told, so that version is a setting and not part of a release. It
is the file `<proxy home>/chatgpt-credential/client-version`, shared by every
account and re-read on every request; with no file the proxy sends its built-in
default (`DEFAULT_CODEX_CLIENT_VERSION` in `cheese_billing_core.py`). The model
list is asked for the same version: the proxy sets `client_version` in its query
to the header's value, replacing whatever the caller sent.

- `chatgpt-login.sh client-version set <version>` sets it from the next
  request, for example to the Codex release whose models the platform should see.
- `chatgpt-login.sh client-version show` prints it.
- `chatgpt-login.sh client-version clear` goes back to the built-in default.
