# Boot-persistent services (systemd)

Runs the full stack on boot and gives a clean `systemctl` restart instead of manual
`kill`. Three system units (run as user `nictheboy`), chained so each waits for the last:

| unit | what it does |
|------|--------------|
| `cheese-infra.service`    | oneshot `docker compose up -d` — Postgres / Valkey / Elasticsearch. Requires `docker.service`. |
| `cheese-backend.service`  | `uv run uvicorn app.main:app --host 0.0.0.0 --port 8080`. Waits for Postgres (`pg_isready`) first. `Restart=always`. |
| `cheese-frontend.service` | `vite --host 0.0.0.0 --port 3000` (dev server; also proxies `/api` and `/connector` to the backend). `Restart=always`. |

Boot order: `docker.service` → `cheese-infra` (compose up) → `cheese-backend` (after PG
ready) → `cheese-frontend`.

## Install

Paths inside the units are absolute to this checkout
(`/home/nictheboy/repo/SageSeekerSociety/cheese-backend-py`) and user `nictheboy`; edit
them if the deploy location or user differs.

```bash
sudo cp misc/deploy/systemd/cheese-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cheese-infra cheese-backend cheese-frontend
```

## Operate (the clean way — no more manual kill)

```bash
sudo systemctl restart cheese-backend      # pull code, then restart backend cleanly
sudo systemctl restart cheese-frontend
sudo systemctl restart cheese-infra        # re-run docker compose up
systemctl status  cheese-backend           # health
journalctl -u cheese-backend -f            # live logs
sudo systemctl stop/start cheese-backend
```

After a `git pull` that changes deps or schema, restart in order:
`cheese-infra` (if compose changed) → run `alembic upgrade head` →
`sudo systemctl restart cheese-backend cheese-frontend`.

## Notes

- The units are enabled, so a server reboot brings the whole stack back with no manual
  steps. Verified: all three `enabled` + `active`; the site answers on `:3000` and the
  backend on `:8080`.
- The container **boot** start is owned by `cheese-infra`. The compose services do not set
  a `restart:` policy, so a *mid-run container crash* (not a reboot) is not auto-healed; add
  `restart: unless-stopped` in `docker-compose.yml` if you want that too.
- Backend port is **8080** because the vite dev proxy rewrites `/api → localhost:8080`
  (see `CLAUDE.md`). Keep them in sync if you change it.
- Production would front this with nginx/ingress serving the built `dist/` and proxying
  `/api` + `/connector`, replacing the vite dev server; these units match the current
  single-box demo topology.
