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

## Running it

A stack of its own, on purpose: `deploy-docker.sh` takes one compose file and
brings up exactly `backend frontend`, and that file is shared with prod. App
deploys never touch the gateway, and restarting the gateway never restarts the app.

The existing image build pipeline builds `gateway` when `deploy/gateway/`
changes. Otherwise, it reuses the previous gateway image and publishes it under
the current commit tag. The Docker build runs
the adapter and timing tests without provider requests.

For the existing dev gateway, use the **Release gateway** workflow on `main`.
Supply the full SHA of a commit on `main` whose image build succeeded and explicitly
acknowledge stream interruption. Replacing the gateway can interrupt active model streams; it does
not restart the application or gateway database. This workflow does not target
production.

The release reads credentials from `$HOME/gateway/compose/.env`, pulls the
CI-built image, saves the running image ID and configuration under
`$HOME/gateway/releases/`, and tests image preservation before changing the service.
It uses the configuration bundled in the image. If the new container fails its
health check, the release restores the saved image and configuration and reports
failure. Both the old and new configurations remain in the release directory.

The box's `backend/.env` must contain:

```
LLM_GATEWAY_ADMIN_BASE=http://litellm:4000
LLM_GATEWAY_ADMIN_KEY=<LITELLM_MASTER_KEY from $HOME/gateway/compose/.env>
```

Changes to these settings take effect through the application deployment pipeline.

The `deepseek-flash` entry declares its thinking and effort capabilities. Without
them, the pinned gateway removes the thinking settings sent by Claude Code.

## Putting a model in front of people

Add it to `config.yaml` with its route and its price, and mark it
`cheese_selectable: true` under `model_info`. That is the whole change: cheese
reads this file back through `/model/info` and keeps no list of its own, so
nothing in the backend has to be edited or shipped for the model to appear in
agent settings. The marker is opt-in because the gateway also routes models that
are not menu items — `glm-4.5` is where the subagent alias points.

Adding a model through the gateway's admin API instead (`STORE_MODEL_IN_DB` is
on) routes it, but does not offer it to anyone: that path skips config.yaml and
with it the review of the price. Opening it is a deliberate decision, and one
clause in `LlmGateway.models` — not something to discover by accident.

A selectable model with no price is not offered at all. Its tokens would meter
at zero, the project's `max_budget` would never trip, and the first sign of
trouble would be the invoice; a model missing from the picker gets noticed, a
brake that quietly stopped working does not. `check_config.py` asserts this for
every selectable entry, so run it after editing the list.

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
docker exec -i cheese-gateway-litellm-1 python - /app/config.yaml < deploy/gateway/check_config.py
```

## Two things that will bite

**No published port.** The gateway joins the app's docker network instead. A
host binding is either loopback, which containers cannot reach (the app sits on
its own bridge), or a bridge address, which puts the upstream keys and the admin
API on the box's LAN. Joining the network removes the choice.

**Image versions.** CI builds from an upstream digest and publishes a commit tag.
The release workflow verifies main ancestry and a successful build before pulling
that tag. It does not build or retag an image on the deployment host.
