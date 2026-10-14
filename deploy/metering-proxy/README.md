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
- `compose.yml` — the front and the two proxy slots. Values via a box-local
  `.env` (see header).
- `front.cfg` — the front's HAProxy configuration (see "Release on the dev box").

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

## Who reaches which listener

One session listener, `:8444` CONNECT: a session's `claude` runs on the session
host and is steered by `HTTPS_PROXY` to its seat's tunnel helper, which carries
the CONNECT over `/llm/tunnel`.

That process has no root, no docker and no `/etc/hosts` to rewrite, so
CONNECT is its only route. It also cannot set `ANTHROPIC_BASE_URL` instead:
that flips Claude Code into API-key mode, where it ignores the OAuth token
entirely — so a subscription turn *must* be steered at the transport layer.

No machine dials `:8444` itself. Its `claude` points HTTPS_PROXY at a helper on
its own loopback, the helper carries the CONNECT stream over the model tunnel on
the same base the machine reaches the backend at, and the tunnel connects to
`:8444` on the bridge address. So the listener is bridge-only.

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
- **The token cap is a backstop, not a budget**: `CHEESE_TOKEN_CAP` (0 = off,
  the default) counts the rolling window's completed turns plus the turns now
  running, and refuses a `/v1/messages` turn before forwarding once that total
  reaches the cap. In-flight turns are held at an estimate, so concurrent turns
  are refused together instead of each passing on the same stale total — but a
  turn already admitted runs to its end, so the window can overshoot by the
  difference between an estimate and the real cost. It is deployment-wide and
  per-project-agnostic: one project's concurrency spends another's headroom,
  and when the window fills the whole deployment's subscription traffic is
  refused until it rolls. Per-project budgets are admission's job. The gateway
  pool is not under this window at all — LiteLLM meters it.
- **One ledger, written whole**: during a release the old and the new proxy
  both append to `usage.jsonl`. Each row goes out in one `write` under an
  exclusive lock on the file, so rows never interleave and the backend's ingest,
  which reads by byte offset, sees whole lines only. With the cap on, each
  proxy also reads the rows the other appended, so a turn the old proxy
  finishes counts against the cap the new one enforces.

## Release on the dev box

The dev application deployment releases the metering image after updating the
application, using the same full commit SHA and requiring its image build and
Required CI to succeed. The **Release metering proxy** GitHub Actions workflow
does the same for a SHA given by hand.

Callers never reach a proxy directly. `front` (HAProxy, `front.cfg`) holds
`172.17.0.1:8444` and the alias `metering-proxy` on `cheese-meter-gateway`, and
behind it run two proxies, `slot-blue` and `slot-green`; outside a release only
one of them runs. A release (`deploy/release-metering-proxy.sh`):

1. starts the idle slot on the new image, held out of rotation until it is
   healthy and the front sees it up;
2. moves new connections to it through the front's runtime API, without a
   reload, so every open connection stays where it is;
3. marks the old slot draining: it then closes each client connection after
   the response that connection carries, so a client's next request opens a
   new connection and lands on the new slot;
4. stops the old slot once it has no connection open, counted in the slot's own
   network namespace, or after `METERING_DRAIN_SECONDS` (1800) with whatever is
   still open cut. Clients close idle connections within a few minutes, so a
   drain usually lasts as long as the longest stream running at the switch.

A slot that fails its health check never receives traffic; rolling back is
releasing the previous SHA. A slot already serving the same digest with the same
configuration is left running. The configuration is the compose file and the
box-local `.env`: each release labels the slot it starts with a SHA-256 of the
two (`cheese.metering-proxy.config-sha256`), so a change to either alone is
released too. Only the hash is stored; the `.env` is never printed or copied.

A change to `front.cfg` is applied by a hitless reload (the slots' state is
carried over in a server-state file). A change to the front's image or service
definition recreates the front, which cuts every open connection: the automatic
deployment refuses it, and the manual workflow runs it only with its
`interrupt` input set.

### What the two slots share while they overlap

| State | Where it lives | During the overlap |
| --- | --- | --- |
| Ledger `usage.jsonl` | shared file | appended whole rows under a file lock; ingest unchanged |
| Claude and ChatGPT credentials | shared files | a refresh holds a file lock and re-reads first, so one pair is refreshed once between the two proxies |
| Claude account cooldowns (`cooldowns.json`) | shared file | each change is applied to the file as it stands, under a file lock |
| Rolling token window (cap on) | memory, rebuilt from the ledger | each proxy reads the rows the other appends |
| Turns in flight against the cap | memory | not shared; the new proxy does not see the old one's running turns, so the cap can overshoot by them, as it already can for any turn admitted before the window filled |
| Conversation-to-account affinity, single-probe recovery | memory | not shared; a conversation that reconnects may land on another account once, as after any restart |
| Spent ChatGPT accounts | memory | not shared; the new proxy learns a spent account from its first refusal, as after any restart |
| Admission cache, egress health | memory | rebuilt within seconds |

The old slot takes no new connections, and each HTTP connection it holds
carries at most one more request after the switch, so what remains per process is bounded
by the requests it finishes.

### The move from the single container

Before this layout the proxy ran as one container, `cheese-metering-proxy`,
holding the address and the alias itself, and Docker moves neither while it
runs. The first release after the change moves the box without cutting a
session's stream: it starts `slot-blue` and a second front, `front-handover`;
inserts a NAT rule (`iptables -t nat`, PREROUTING and OUTPUT, comment
`cheese-metering-handover`) that sends new connections for `172.17.0.1:8444` to
`front-handover`, while connections the old container already has keep their
translation; waits until the old container has none left (bounded by the same
drain limit); stops it; starts `front` on the freed address; removes the rule;
and stops `front-handover` once its tunnels have ended. A failure before the old
container stops removes the rule and leaves it serving. On the ChatGPT listener
both the old container and `front-handover` answer the alias meanwhile; the old
container is stopped at a moment it has no connection open on it when one comes
within a minute, and a request it is carrying otherwise is cut.

After a release, verify a sandbox turn, a usage row attributed to that turn, and
a budget refusal for a test project whose compute grant is exhausted. Listener
health alone does not verify that the platform's credential reaches Anthropic,
backend admission or ledger ingestion. Do not print the environment or replace
the CA during a release; both slots use the same CA from `CERTS_DIR`.

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
direct instead. Before a request goes out through it, the proxy checks that the
egress accepts a connection, with three short attempts so that a blip of a few
seconds does not fail the turn. An egress that does not answer gets a 503 with
`retry-after`, so the client waits and asks again instead of ending the turn; a
turn that runs out of retries still shows the notice that the subscription's
egress machine is offline. That verdict lasts five seconds, so the machine is
used again as soon as it is back.

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

When ChatGPT answers an account's turn with a 429 of type `usage_limit_reached`,
the proxy remembers that the account is spent until the reset the answer names
(`resets_at` or `resets_in_seconds`). Until then it answers that account's turns
itself, at once, with a 429 of the same type and a `Retry-After`, so the gateway
moves to another account without reaching ChatGPT. A plan can open again early
and nothing announces it, so every `CHEESE_CHATGPT_LIMIT_PROBE_S` seconds (600 by
default) the proxy asks the account's usage at
`https://chatgpt.com/backend-api/wham/usage` (what Codex's /status reads), on the
account's own login and egress, and clears the record as soon as it reports
capacity. No user turn is sent to a spent account for that. Only when the usage
cannot be read does the next look send one real turn instead. Any turn the
account serves clears the record. Other 429s and the model list are passed as
before.
The record lives in memory: a restarted proxy learns it again from the next
refusal.
