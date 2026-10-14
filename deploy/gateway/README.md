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
serving color lists, apart from any this release removes from `config.yaml`,
so a model added on the admin page has loaded from the database. `/health` is
not used: it calls every model. Then one runtime-API
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
flags included); both listen on `:4000` (SO_REUSEPORT). Once the new one sees
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
is started again. The metering proxy's idle connections to the old container
die with it, so the next request on each fails once.

Kimi K3 uses the native Messages endpoint at
`https://api.moonshot.cn/anthropic`. Set `MOONSHOT_API_KEY` in the gateway's
`$HOME/gateway/compose/.env` before releasing a configuration that includes it.
Its budget prices convert the published CNY rates at the existing RMB 7.1/USD
convention; they are estimates, not a live exchange rate. The pinned adapter
keeps `output_config.effort` for `low`, `high`, and `max`, and removes the
unsupported adaptive-thinking field.

`deepseek-flash` is served by RUC's self-hosted copy first, one deployment per
RUC key, and by DeepSeek's API when every RUC key fails or is past its 3
parallel requests. Set `RUC_DEEPSEEK_API_BASE` (the endpoint's address, no
path) and `RUC_DEEPSEEK_API_KEY_1` to `RUC_DEEPSEEK_API_KEY_9` in
`$HOME/gateway/compose/.env` before releasing: nine of the ten keys RUC
issued, all but the one kept for tests. The endpoint admits campus
addresses only, so a box serving it needs the route in docs/infrastructure.md
("Campus model API").

The gateway also joins `cheese-meter-gateway`, an internal network shared only
with the metering proxy, where the proxy's ChatGPT accounts answer at
`http://metering-proxy:8445/chatgpt/<name>` (deploy/metering-proxy/README.md).
The release creates the network if the proxy's release has not.

A request to a model routed there carries the caller's Claude Code session as
the `session_id` header (`chatgpt_session.py`, registered under
`litellm_settings.callbacks`). The Codex backend keeps a conversation on one
machine by that header, and without it the conversation's prompt cache misses.

A request to a model whose upstreams are all something other than Anthropic's
API arrives there without the block Claude Code opens its system prompt with,
`x-anthropic-billing-header: ...; cch=<hash>; ...` (`system_billing_line.py`,
registered the same way). `cch` hashes the request body, so it changes on every
request; at the head of the prompt it makes a prefix cache such as RUC's miss
the whole conversation. Requests bound for Anthropic keep it.

The box's `backend/.env` must contain:

```
LLM_GATEWAY_ADMIN_BASE=http://litellm:4000
LLM_GATEWAY_ADMIN_KEY=<LITELLM_MASTER_KEY from $HOME/gateway/compose/.env>
```

Changes to these settings take effect through the application deployment pipeline.

The `deepseek-flash` entry declares its thinking and effort capabilities. Without
them, the pinned gateway removes the thinking settings sent by Claude Code.

## Putting a model in front of people

There are two ways to add one, and both end in the same place: cheese reads the
gateway back through `/model/info` and keeps no list of its own, so a model
appears in agent settings as soon as the gateway reports it, without a backend
release.

**In `config.yaml`** — add the route and the price and mark it
`cheese_selectable: true` under `model_info`. This is the baseline that ships
with the image, so a deployment's always-on models belong here; changing it
means releasing the gateway. The marker is opt-in because the
gateway also routes models that are not menu items — `glm-4.5` is where the
subagent alias points.

A model whose route loses the system-role messages Claude Code puts in the
middle of a conversation is marked `supports_mid_conversation_system: false`
under `model_info` (or with the switch on the admin page). Mark it only after
measuring it; cheese then launches Claude Code to put that content in the first
user message. See `docs/manual/dev/gateway.md`.

**On the admin models page** — administrators add, edit, disable, and delete
runtime models (`STORE_MODEL_IN_DB` is on) without a release. Every write is
audited with the acting handle, and a successful write refreshes the catalogue
so the model is pickable immediately. Models declared in `config.yaml` appear
there read-only, because the gateway refuses to write them.

Both routes owe one invariant, and the service behind the admin page enforces it
as surely as `check_config.py` enforces it for the file: a selectable model with
no price is not offered at all. Its tokens would meter at zero, the project's
`max_budget` would never trip, and the first sign of trouble would be the
invoice; a model missing from the picker gets noticed, a brake that quietly
stopped working does not. `check_config.py` asserts this for every selectable
entry, so run it after editing the list.

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
ends such a stream with an Anthropic `error` event instead; before any content
the router turns it into an HTTP error the caller retries.
`test_responses_stream_errors.py` checks this against a local fake upstream.
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

Check the loaded configuration's request transformation and nonzero token prices:

```sh
docker exec -i cheese-gateway-litellm-blue-1 python - /app/config.yaml < deploy/gateway/check_config.py  # or -green-1, whichever serves
```

## Prices for subscription models

The Claude models served through the platform's subscription never pass
through this gateway, but their usage is charged at the same prices as gateway
traffic. `config.yaml` therefore carries a price-only entry for each of them:
`model_info.blocked: true`, so the router refuses to route it, and no
credential. The backend reads these prices back through `/model/info` when it
ingests the metering proxy's usage log. A new subscription model needs an entry
here; until it has one, its usage is recorded and nothing is charged.
`check_config.py` asserts that every blocked entry is refused by the router and
carries every price: input, output, cache read, and the 5-minute and 1-hour
cache writes.

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
rate-limit, and documented quota errors; an unknown HTTP 500 is not labeled a
network cause. Timing completion means HTTP EOF, while the supply probe also
checks the Messages terminal event.

## Two things that will bite

**The port is on the docker0 bridge address.** `gateway-netns` publishes
`172.17.0.1:4000`: sandbox containers sit on the default bridge and cannot use
the app network's alias, loopback is unreachable from any container, and
`0.0.0.0` would put the admin API on the box's LAN.

**Image versions.** CI builds from an upstream digest and publishes a commit tag.
The release workflow verifies main ancestry and a successful build before pulling
that tag. It does not build or retag an image on the deployment host.
