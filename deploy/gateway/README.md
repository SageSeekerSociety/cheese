# The LLM gateway

Cheese ran for weeks with no idea what it was spending. The usage table held
four rows across two weeks while 300 RMB of model credit drained, and nothing
in it named a project, a turn, or a token.

That was not a bug. The hooks backends run *interactive* Claude Code, which
reports no token usage locally, so the design reads spend from a self-hosted
gateway instead (`backend/app/domain/agent/gateway.py`). The code was written
and the settings were reserved — the gateway itself was never deployed, so
`gateway` resolved to `None` and every usage row was quietly skipped.

## What it gives you

- **Accounting** — a row per call in the gateway's own database, drained into
  `resource_usage` as a per-project daily cumulative delta.
- **Attribution** — a virtual key per project, so spend is attributable and a
  sandbox never holds the upstream key.
- **A brake** — `max_budget` on that key, so the gateway refuses calls once a
  project's grant is spent, rather than discovering it on the invoice.

The brake compares `max_budget` with a per-key spend counter, which lives in the
stack's own Redis (`litellm-redis`, wired in as `coordination_redis`). Without
one, LiteLLM keeps the counter in process memory, where seeding a cold counter
can race a repair and double it, refusing a key at half its budget until the
counter expires. The Redis holds nothing that cannot be rebuilt: a missing
counter is reseeded from the gateway database, so it runs without persistence.

## Running it

A stack of its own, on purpose: `deploy-docker.sh` takes one compose file and
brings up exactly `backend frontend`, and that file is shared with prod. App
deploys never touch the gateway, and restarting the gateway never restarts the app.

The existing image build pipeline builds `gateway` when `deploy/gateway/`
changes. Otherwise, it reuses the previous gateway image and publishes it under
the current commit tag. The Docker build runs
the adapter and timing tests without provider requests.

For the existing dev gateway, use the Release gateway workflow on `main`.
Supply the full SHA of a commit on `main` whose Build and Required CI succeeded.
A release does not interrupt model streams and does not restart the application
or gateway database. This workflow does not target production.

Callers reach `172.17.0.1:4000` and, on the app network, `http://litellm:4000`,
an address `gateway-netns` owns and nothing else does: it holds the network
namespace in which HAProxy (`gateway-proxy-a` or `-b`,
`deploy/compose/gateway-proxy.cfg`) runs. Behind the proxy run two LiteLLM
instances, `litellm-blue` and `litellm-green`, sharing the gateway database and
Redis. Outside a release one proxy and one color run.

Only one color runs at a time on purpose. LiteLLM keeps router cooldowns and
the `deepseek-flash` session affinity in process memory (`router_settings` has
no Redis), so two colors taking traffic would each keep their own, and a
conversation would bounce between RUC nodes and keep missing its prefix cache.
A second standing color would only help if the gateway crashed on its own;
that case would also need the router state moved to Redis first.

A release (`deploy/release-gateway.sh`) reads credentials from
`$HOME/gateway/compose/.env`, pulls the CI-built image, tests image
preservation, and starts the idle color with the configuration bundled in the
image. The new color takes traffic only after four checks: its Docker health
check, `/health/readiness`, and the proxy's own check, plus a `GET /v1/models`
sent to it with the master key. That listing must include every model the
serving color lists, so a model added on the admin page has loaded from the
database, and the new color must offer at least one selectable model. The only
model allowed to go missing is one this release removes from `config.yaml`, no
database row of the serving color serves (`/model/info`, `db_model`), and the
operator names in the workflow's `allow_drop` input (`GATEWAY_ALLOW_DROP`).
`/health` is not used: it calls every model. Then one runtime-API
command makes the new color serve and sets the old one to drain. The proxy is
not restarted, so no caller connection closes, and the next request on any of
them goes to the new color. The old color finishes the streams it has; the
release stops it when the proxy counts no open request on it, or after 30
minutes, in which case it says in a workflow warning how many requests that
cut. A new color that fails any check is removed and traffic never leaves the
old one. To roll back, release the previous SHA the same way.

The proxy retries a request on the other color only when the connection itself
failed (`retry-on conn-failure`, the HAProxy 3.2 default, written out). It
never retries a request a color may have started, because LiteLLM may already
have called the provider and a second attempt would be billed twice. Its
health check is `/health/liveliness`; readiness fails while the database is out
of reach, which would take both colors out at once.

The colors have fixed addresses on an internal network rather than being found
by name. Docker's DNS passes names it does not know to the host's resolver, and
the dev box's resolver answers every name with an address (`198.18.x`), so a
stopped color's name would still resolve and requests would go to that address.

A change to the proxy (its configuration, image or service definition) does not
interrupt anything either. The release starts the other proxy in the same
network namespace with the colors' current states (`server-state-file`, drain
flags included), and deletes the file once that proxy is up: colors switch
through the runtime API afterwards, so a later cold start (a reboot, a crash
restart) reading it would bring the serving color back drained. Both proxies
listen on `:4000` (SO_REUSEPORT). Once the new one sees
the serving color, the old one is soft-stopped: it stops accepting at once,
connections already queued on its listener move to the new one
(`net.ipv4.tcp_migrate_req`), it serves the connections it has, answers the next
request on each idle one with `Connection: close`
(`option idle-close-on-response`), and exits when they end or after 35 minutes
(`hard-stop-after`). The old color is stopped only after that proxy has
exited, because its streams are on no count the new proxy keeps.

Two cases interrupt streams, and the workflow refuses them unless its
`interrupt` box is ticked: replacing `gateway-netns` itself, when its port,
networks or image change, and the one release that moves a host from the single
`cheese-gateway-litellm-1` container to this layout. That container holds the
port and the `litellm` alias, and Docker cannot move them off a running
container. The move starts blue, waits up to 10 minutes for a moment when no
connection to the old container has carried data for 30 seconds, read with
`ss` in its network namespace through sudo, stops it with 30 seconds for
in-flight requests, during which new requests are refused, and starts
`gateway-netns` and the proxy in its place; if the proxy does not come up, the old container
is started again, and so it is if the release is cancelled in between. The metering proxy's idle connections to the old container
die with it, so the next request on each fails once.

Kimi K3 uses the native Messages endpoint at
`https://api.moonshot.cn/anthropic`. Set `MOONSHOT_API_KEY` in the gateway's
`$HOME/gateway/compose/.env`; its row names it as `os.environ/MOONSHOT_API_KEY`.
Its budget prices convert the published CNY rates at the existing RMB 7.1/USD
convention; they are estimates, not a live exchange rate. The pinned adapter
keeps `output_config.effort` for `low`, `high`, and `max`, and removes the
unsupported adaptive-thinking field.

Gemini 4 Argon runs on Google AI Studio. Its row's `api_key` is
`os.environ/GEMINI_API_KEY`, so `GEMINI_API_KEY` stays in the same `.env`.
Its prices are Google's rates from 2026-12-01 ($4 in, $20 out, $0.20 cache
read per million tokens; https://ai.google.dev/gemini-api/docs/pricing read
2026-10-15), half that until then, so the brake does not go soft on 12-01.
Measured 2026-10-15 with `gateway_conformance.py`: text, tool calls, parallel
calls, tool results, images in tool results, the mid-conversation system
message and the implicit prompt cache pass. Two adaptations make
Claude Code's requests work there: `patch_gemini_generation.py` makes the
pinned LiteLLM treat `gemini-4` as the Gemini 3+ generation (thought
signatures, tool-call ids, thinking level), and `no_explicit_cache.py` strips
`cache_control` from a deployment marked `cheese_no_explicit_cache: true`,
because argon refuses explicit caching and LiteLLM would otherwise create a
`cachedContents` object for every Claude Code request. Its key has a lifetime
cap (`cheese_spend_cap_usd`, `spend_cap.py`; USD 2,000 when the row was added),
counted from the gateway database's daily spend, so the cap survives releases;
change it on the admin models page (see "Putting a model in front of people").

`deepseek-flash` is served by RUC's self-hosted copy first, one deployment per
RUC key, and by DeepSeek's API when every RUC key fails or is past its 3
parallel requests. Set `RUC_DEEPSEEK_API_BASE` (the endpoint's address, no
path) and `RUC_DEEPSEEK_API_KEY_1` to `RUC_DEEPSEEK_API_KEY_9` in
`$HOME/gateway/compose/.env`, which its rows name: nine of the ten keys RUC
issued, all but the one kept for tests. The endpoint admits campus
addresses only, so a box serving it needs the route in docs/infrastructure.md
("Campus model API").

A row whose provider account has run out of money (DeepSeek's `400
Insufficient Balance`, Zhipu's `[1113]`, Volcengine's `SetLimitExceeded`,
Moonshot's suspended account, OpenAI's `insufficient_quota` and the like) is
taken out of rotation for ten minutes, after which one request tries the
account again (`provider_credit.py`); the router's own cooldown is seconds,
and none at all for a 400. Requests meanwhile go to the model's other rows by
`order`. A request that ran into the empty row while another row is only busy
is answered 429 with that row's Retry-After, so Claude Code's retry reaches it;
once every row of a model is out of credit, the answer is a 402 whose message
starts `cheese_upstream_credit_exhausted`, which Claude Code does not retry.
Every row the gateway finds empty is named in the
`x-cheese-upstream-out-of-credit` response header; the metering proxy reports
each one to the backend, which alerts the administrators once per row.

`qwen-audio-asr` uses Alibaba Bailian (Beijing) for the composer's voice input.
Set `DASHSCOPE_API_KEY` in `$HOME/gateway/compose/.env`. A workspace that was
given its own dedicated endpoint also sets `DASHSCOPE_API_BASE` there (for
example `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`, whose key is the
workspace's own); unset, the shared DashScope domain is used. Neither the
workspace id nor the key belongs in this repository.

The gateway also joins `cheese-meter-gateway`, an internal network shared only
with the metering proxy, where the proxy's ChatGPT accounts answer at
`http://metering-proxy:8445/chatgpt/<name>` (deploy/metering-proxy/README.md).
The release creates the network if the proxy's release has not.

A request to a model routed there carries the caller's Claude Code session as
the `session_id` header (`chatgpt_session.py`, registered under
`litellm_settings.callbacks`). The Codex backend keeps a conversation on one
machine by that header, and without it the conversation's prompt cache misses.

The Codex backend sometimes streams a function call without any argument
deltas, sending the arguments only on the `done` events. The image patches
LiteLLM's Responses-to-Messages stream adapter (`patch_responses_done_args.py`)
to forward those arguments once when no delta came; without it such a tool call
reaches Claude Code with an empty input.

A request to a model whose upstreams are all something other than Anthropic's
API arrives there without the block Claude Code opens its system prompt with,
`x-anthropic-billing-header: ...; cch=<hash>; ...` (`system_billing_line.py`,
registered the same way). `cch` hashes the request body, so it changes on every
request; at the head of the prompt it makes a prefix cache such as RUC's miss
the whole conversation. Requests bound for Anthropic keep it.

Every inline image in a request is opened with Pillow before the request goes
upstream (`image_guard.py`, registered the same way). An image that is not PNG,
JPEG, GIF or WebP, does not decode, or is over 8000 px on a side is replaced by
a text block naming the reason and the image's hash, and the gateway logs one
`image_guard replaced image` line for it. Every provider refuses a request with
such an image in it, and Claude Code resends its whole history on each turn, so
without this one broken picture fails every later request of a conversation.
The replacement depends only on the image's bytes, so it is the same on every
turn and the prompt cache keeps hitting. Images that open pass unchanged, and
an image given by URL is not fetched.

DeepSeek's Anthropic-compatible API runs thinking on by default and refuses a
request when an assistant message no longer carries its thinking block
(`deepseek_thinking.py`, installed from `LITELLM_WORKER_STARTUP_HOOKS`). Claude
Code resends its whole history on every turn and some of its own paths drop
those blocks, so one lost block fails every later request of a conversation
with `The content[].thinking in the thinking mode must be passed back to the
API`. The repair wraps DeepSeek's own Messages transformation, so it follows
the provider rather than a model name: an assistant message without a thinking
block — or with an empty one — gets `(thinking omitted)` and an empty
signature, which DeepSeek accepts. A message that still has its thinking passes
untouched, and a request that asked for thinking off gains nothing. The gateway
logs one `deepseek_thinking filled a missing thinking block` line per repaired
request.

A deployment that declares `cheese_upstream_headers` or `cheese_session_header`
under its `model_info` is sent those headers, and no other row of its model group
is (`upstream_headers.py`, registered the same way). OpenCode Go needs both: its
own User-Agent, and a session per conversation without which it refuses the
request. How to put a subscription or relay row in front of a pay-per-use one:
`docs/manual/dev/gateway.md`, "订阅与中转".

Claude Code's WebSearch asks the upstream to run the search (the server tool
`web_search_20250305`). DeepSeek's API runs it; RUC's vLLM does not, and
answers with the model's text alone, which Claude Code shows the agent as a
search that found nothing. So a deployment that cannot search says
`supports_web_search: false` under `model_info`, a request whose tools include
web search is not routed to it (`web_search.py`, registered the same way), and
an answer to a search-only request that comes back with neither a
`server_tool_use` block nor a search counted in its usage (how LiteLLM reports
a search it translated, such as Gemini's grounding) reaches the client as an
error, logged as `cheese web_search not run`
with the model group and deployment. Mark a new deployment only after
measuring it.

The box's `backend/.env` must contain:

```
LLM_GATEWAY_ADMIN_BASE=http://litellm:4000
LLM_GATEWAY_ADMIN_KEY=<LITELLM_MASTER_KEY from $HOME/gateway/compose/.env>
```

Changes to these settings take effect through the application deployment pipeline.

The `deepseek-flash` entry declares its thinking and effort capabilities. Without
them, the pinned gateway removes the thinking settings sent by Claude Code.

## Putting a model in front of people

Add it on the admin models page. `config.yaml` declares no models: every model
the gateway routes, and every price-only entry, is a row in the gateway
database (`STORE_MODEL_IN_DB` is on), so adding a model or changing its price
needs no release. Cheese reads the gateway back through `/model/info` and keeps
no list of its own, so a model appears in agent settings as soon as the gateway
reports it. Administrators add, edit, disable, and delete models there; every
write is audited with the acting handle, and a successful write refreshes the
catalogue so the model is pickable immediately. Mark a model
`cheese_selectable: true` to offer it; the marker is opt-in because the gateway
also routes models the platform calls on someone's behalf. The cheese keys under
`model_info` are listed in `docs/manual/dev/gateway.md`.

A row names its credential as `os.environ/<VAR>`, which the pinned LiteLLM
resolves from the gateway's environment for database rows
(`ProxyConfig._resolve_db_litellm_param`), so provider keys stay in
`$HOME/gateway/compose/.env` and never enter the database. The database is the
only record of which models exist and what they cost: keep it backed up.

A model whose route loses the system-role messages Claude Code puts in the
middle of a conversation is marked `supports_mid_conversation_system: false`
with the switch on the admin page. Mark it only after measuring it; cheese then
launches Claude Code to put that content in the first user message. See
`docs/manual/dev/gateway.md`.

A spend cap (`cheese_spend_cap_usd`, enforced by `spend_cap.py`) is changed the
same way: edit any runtime source on that upstream model and set its spend cap,
or clear it for no cap; the API equivalent is
`PATCH /admin/gateway/models/{name}/sources/{source_id}` with
`{"spend_cap_usd": 3000}` or `{"spend_cap_usd": null}`. The cap counts
everything the upstream model (`litellm_params.model`) has been charged, whichever
row carried the calls, so the edit writes the same cap on every runtime row on
that upstream, under any model name. A source added to a model beside a row on
the same upstream copies that row's cap.
A `config.yaml` row on the same upstream cannot be rewritten from the page; while
one declares a different cap, the edit is refused.

The service behind the admin page holds runtime rows to one invariant above all: a selectable model with
no price is not offered at all, unless its upstream is free and it says so with
`cheese_free: true` and both prices written as 0. Its tokens would meter at zero, the project's
`max_budget` would never trip, and the first sign of trouble would be the
invoice; a model missing from the picker gets noticed, a brake that quietly
stopped working does not.

The other rules `check_config.py` used to hold the file to are held at the
write as well. Every row, offered or not, needs both an input and an output
price (the only zeros are a declared free row's), because usage under a
model's name is billed by them, the subscription's Claude rows included; a
price a row has can be changed, never cleared, and the page edits the
one-hour cache-write price too. A price change on the page is confirmed
first, showing each price old to new. The last offered model cannot be
paused, deleted or unlisted, and a model a platform model role runs on (voice
input, the default model, ...) cannot be paused or deleted until the role
points elsewhere. One rule is held by nothing:
deepseek-flash's `max_input_tokens` must leave 128,000 output tokens inside
DeepSeek's 1,048,576 window. The page does not write that key, so only a row
written straight to the gateway can break it; check it there.

Saving a different model refreshes the native session at the next task
boundary; the scoped session credential carries the selected model's route.
Auxiliary and subagent model aliases follow the selected API model. API-backed
Remote Control sessions receive Cheese project identity and control policy from
the metering proxy; these queries do not require an Anthropic login. Claude
subscription sessions retain their provider account route.

The central session's `AGENT_SESSION_API_BASE` must also be reachable from its
private execution containers. A host loopback URL makes platform tools inside
those containers fail with connection refused; use the deployment's reachable
backend address.

The gateway Dockerfile derives from the upstream image pinned by digest and checks the
streaming logger's file hash before applying a timing correction. The upstream
logger starts its clock when the stream wrapper is created, omitting earlier
request time. The patch retains the logging object's original request start.
An isolated test runs during the image build without calling a model provider.

A model served through OpenAI's Responses API (the ChatGPT accounts) reaches
`/v1/messages` through an adapter that, as pinned, ends the stream quietly when
the upstream drops the connection or reports `response.failed`. The router holds
`message_start` back until content arrives, so the caller got a 200 stream with
no content and no stop, which Claude Code reports as "Stream ended without
receiving any events" and does not retry. `patch_responses_stream_errors.py`
ends such a stream with an Anthropic `error` event instead. A clean HTTP EOF
without a terminal Responses event also emits a retryable `overloaded_error`:
HTTP framing alone cannot prove the answer completed.

Only a text block the failure left open can be recovered: it is closed with a
`content_block_stop` before the error, so a client reading that block retries
the model request. A tool block is deliberately left open, and this applies to both a
complete and a partial `function_call` — emitting a `content_block_stop` for it
would make the caller run the tool instead of retrying. So a failure is not
made recoverable in general: a stream whose only open block is a `tool_use`
still fails. If a text block is also open, closing it lets the client retry the
model request; that unfinished tool is discarded without execution.
Neither content is replayed, nor a successful `message_stop` manufactured, and
a declared token limit keeps its normal `max_tokens` ending.
`test_responses_stream_errors.py` checks these boundaries against a local fake
upstream, including one that fails while a text block is open and asserts the
adapter ends with a single error rather than two.

With no fallback configured, the router then re-raises that failure to the
proxy, which as pinned ends the stream with an unnamed OpenAI-shaped
`data: {"error": ...}` frame. If that frame is the stream's first chunk, the
proxy answers with its HTTP status instead, which callers retry. But a stream
that waits 15 s has already sent a keepalive `ping` and a 200, and Claude Code
ignores an unnamed frame, so the turn still ended with "Stream ended without
receiving any events". `patch_anthropic_stream_errors.py` re-frames an error
after the first chunk of a `/v1/messages` stream as `event: error`, typed
`overloaded_error` for a server-side failure, the only streamed error type
Claude Code retries. `test_anthropic_stream_errors.py` drives `/v1/messages`
on the proxy to check it.
These timestamps cover the gateway request; they do not separately measure
provider processing and gateway overhead. Application deployments still leave
this independent gateway stack running.

The derived image also emits `provider_http_timing` records for model HTTP calls,
correlated by LiteLLM call ID. They mark entry into the HTTP request, response
headers (or a buffered response), and stream completion, interruption, or early
closure. Logs contain timestamps, duration, status, and outcome; they omit URLs,
headers, credentials, and bodies. The interval includes connection setup, HTTP
retries, network transfer, and stream-consumer delays. It is an upstream HTTP
interval, not a measurement of provider compute alone. Offline build tests verify
body delivery, close propagation, cancellation, failures, and log redaction.

The image build runs `check_config.py`, which checks the pinned LiteLLM's
behaviour on deployments it builds itself: DeepSeek thinking, Kimi effort, MiMo
thinking, the cache-write bill, the listed DeepSeek window, and a blocked
price-only deployment refused by the router. It reads no real row.

## Prices for web searches

Claude Code's WebSearch is run by the upstream behind a row, and an upstream
that bills searches bills each one on top of the tokens. A row prices them in
`model_info.search_context_cost_per_query` (USD per search, the same in all
three sizes), and the search cost lands in the call's spend, so it is charged
in credits and counted by the key's budget like the tokens are. DeepSeek's
official row carries $0.000704 a search: ¥5 per 1,000 successful requests on
the Chinese price list, at RMB 7.1/USD, since our account is billed in CNY.
The pinned LiteLLM prices one search per answer for `deepseek` however many
the answer reports; `patch_deepseek_search_cost.py` makes it count
`usage.server_tool_use.web_search_requests`, as it does for Anthropic, and
`test_deepseek_search_cost.py` holds the charge to tokens plus each search. A
row that cannot search needs no price.

The platform's own web search (`cheese_web(action=search)`, backend `/web-search`) is
asked here too, at `/v1/search/deepseek-search` on the caller's own virtual
key: `search_tools` in `config.yaml` names DeepSeek's search API, which the
pinned LiteLLM has no provider for, so `deepseek_search.py` is one and
`patch_deepseek_search_provider.py` registers it. Each search that found
something is priced at the same $0.000704 and logged on the key as model
`deepseek/search`, so the key's budget refuses it past its grant and the
backend drains it into credits with the key's model calls; a search that found
nothing costs nothing, as DeepSeek bills it. `test_deepseek_search.py` holds
both.

## Prices for subscription models

The Claude models served through the platform's subscription never pass
through this gateway, but their usage is charged at the same prices as gateway
traffic. The gateway therefore carries a price-only row for each of them,
disabled on the admin page so the router refuses to route it, with no
credential and every price: input, output, cache read, and the 5-minute and
1-hour cache writes. The backend reads these prices back through `/model/info`
when it ingests the metering proxy's usage log. A new subscription model needs
such a row; until it has one, its usage is recorded and nothing is charged. A
database row is disabled through `POST /model/block` (the admin page's
switch): the pinned LiteLLM takes a database row's `blocked` from that column,
not from `model_info.blocked`.

## Health checks

The gateway keeps up to five 20 MB log files; recreating a color does not preserve its stdout history.

The scheduled check calls `/health/readiness` and the authenticated model catalog.
It checks the gateway database and admin authentication without generating tokens;
provider quota and inference remain untested. For a bounded inference check, run
the existing supply probe with `--generate --model kimi-k3`:

```sh
docker exec -i -e GATEWAY_BASE=http://gateway-netns:4000 cheese-gateway-litellm-blue-1 python - --generate --model kimi-k3 < backend/scripts/gateway_supply_probe.py
```

It makes one native Messages request with a 512-token output limit and a 60-second
deadline. A missing terminal event fails the probe, as do authentication, quota,
rate-limit, and transport errors. Reaching the output limit is reported separately
from a broken stream. The probe prints categories and token counts, never provider
error messages or generated text.

Failure records distinguish observed DNS, TLS, timeout, HTTP authentication,
rate-limit, and provider out-of-credit errors (`provider_quota`, read by
`provider_credit.py`'s classifier); an unknown HTTP 500 is not labeled a
network cause. Timing completion means HTTP EOF, while the supply probe also
checks the Messages terminal event.

## Backups

The gateway database is the only copy of the models added on the admin page
and their prices, of every project's virtual key and budget, and of the spend
logs; `config.yaml` brings back only the baseline models.

The Gateway DB backup workflow (`.github/workflows/gateway-db-backup.yml`)
runs `deploy/gateway-db-backup.sh` on the dev runner every 6 hours (00:23,
06:23, 12:23 and 18:23 UTC) and on demand. It dumps `litellm` with the
container's own `pg_dump -Fc`, reads the archive back with `pg_restore --list`,
which must list the data of `LiteLLM_ProxyModelTable` and
`LiteLLM_VerificationToken`, and only then renames it to
`~/backups/gateway/gateway-litellm-<UTC timestamp>.dump`. The box keeps the
newest 8 dumps (2 days; a dump was 0.85 GB on 2026-10-21). Each dump is then copied to the R2 bucket under the
dev prefix (`db/gateway-litellm-<UTC timestamp>.dump`, `deploy/r2-upload.py`),
where the bucket's lifecycle rule deletes it after 30 days. The database and
its local dumps are on the same box, so a failed upload fails the run. The
script's log is `~/backups/gateway/backup.log`.

Restore on the box, from a local dump or one taken from R2:

```sh
C=cheese-gateway-litellm-db-1
DUMP=~/backups/gateway/gateway-litellm-<ts>.dump
# What it holds:
docker exec -i "$C" pg_restore --list < "$DUMP" | grep 'TABLE DATA'
# A few lost rows (a deleted model): restore beside the live database, read
# them, re-add them on the admin page, drop the copy.
docker exec "$C" createdb -U litellm litellm_restore
docker exec -i "$C" pg_restore -U litellm -d litellm_restore --no-owner --exit-on-error < "$DUMP"
docker exec "$C" psql -U litellm -d litellm_restore -c 'SELECT model_name, model_info FROM "LiteLLM_ProxyModelTable"'
docker exec "$C" dropdb -U litellm litellm_restore
# The whole database (destructive: drops and recreates every object in it):
docker exec -i "$C" pg_restore -U litellm -d litellm --clean --if-exists --no-owner --single-transaction --exit-on-error < "$DUMP"
```

A whole-database restore loses everything written after the dump was taken:
spend, keys minted for new projects, and model edits. The gateway reads the
model table every 30 seconds, so the admin models page lists the restored
models within a minute; check it does.

## Two things that will bite

**The port is on the docker0 bridge address.** `gateway-netns` publishes
`172.17.0.1:4000`: sandbox containers sit on the default bridge and cannot use
the app network's alias, loopback is unreachable from any container, and
`0.0.0.0` would put the admin API on the box's LAN.

**Image versions.** CI builds from an upstream digest and publishes a commit tag.
The release workflow verifies main ancestry and a successful build before pulling
that tag. It does not build or retag an image on the deployment host.
