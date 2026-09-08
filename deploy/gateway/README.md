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

```sh
cd deploy/compose
cat > .env <<EOF
LITELLM_MASTER_KEY=sk-...        # also the backend's LLM_GATEWAY_ADMIN_KEY
LITELLM_DB_PASSWORD=...
ZHIPU_API_KEY=...                # the same upstream keys already in use
DEEPSEEK_API_KEY=...
EOF
chmod 600 .env
docker compose -f docker-compose.gateway.yml -p cheese-gateway up -d
```

Then, in the box's `backend/.env`:

```
LLM_GATEWAY_ADMIN_BASE=http://litellm:4000
LLM_GATEWAY_ADMIN_KEY=<the master key above>
```

and recreate the backend — `docker restart` will not do, since environment is
fixed when a container is created, not when its process starts.

The `deepseek-chat` entry declares its thinking and effort capabilities. Without
them, the pinned gateway removes the thinking settings sent by Claude Code.
Check the loaded configuration's request transformation and nonzero token prices:

```sh
docker exec -i cheese-gateway-litellm-1 python - /app/config.yaml < deploy/gateway/check_config.py
```

## Two things that will bite

**No published port.** The gateway joins the app's docker network instead. A
host binding is either loopback, which containers cannot reach (the app sits on
its own bridge), or a bridge address, which puts the upstream keys and the admin
API on the box's LAN. Joining the network removes the choice.

**Pulling the image.** The box is logged into ghcr for its own private images,
and docker offers that credential for every ghcr pull — including public ones,
where it is rejected rather than falling back to anonymous. Pull with an empty
credential store:

```sh
D=$(mktemp -d); DOCKER_CONFIG=$D docker pull ghcr.io/berriai/litellm@sha256:...; rm -rf $D
```

The image is pinned by digest because berriai publishes no tag for the version
this repo pins as a library (checked against the registry), and `main-latest`
moves.
