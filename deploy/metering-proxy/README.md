# Subscription metering proxy

The metering proxy between a sandbox's Claude Code and Anthropic. It swaps
the sandbox's scoped cheese token for the real subscription credential (which
never reaches the sandbox), meters every `/v1/messages` response into
`usage.jsonl` (ingested by the backend — `subscription_ingest`), and refuses a
turn its project cannot afford (backend `/llm/admission`, plus a rolling token
cap as backstop). Design context: issue #218 and
`docs/plans/2026-08-10-usage-unification-design.md`.

For RC-enabled device sessions, the addon also routes native control requests
to Cheese, consumes the intercepted telemetry, and enables the RC feature flags.
See [terminal controls](../../docs/remote-control.md) for the API, deployment order,
recovery behavior and provider-visibility limits.

## Files

- `billing_addon.py` — the mitmproxy addon (glue only).
- `cheese_billing_core.py` — token verify / metering / admission logic, stdlib
  only, unit-tested from `backend/tests/unit/test_metering_proxy_core.py`.
- `compose.yml` — the container. Values via a box-local `.env` (see header).

## One interception point, two destinations (#243)

Every sandbox's traffic passes through here, and the destination is a per-request
decision rather than something pinned into the sandbox's launch environment:

- `subscription` → inject the real credential, egress via ccproxy → Anthropic.
- `gateway` → rewrite to `CHEESE_GATEWAY_BASE` (LiteLLM) with the project's
  virtual key, no subscription credential, no ccproxy hop (domestic providers
  must not be routed through an overseas exit).

The backend decides (`POST /llm/admission` answers both "may it run" and "where
does it go"); this proxy only carries it out. That is why a project can change
supply without restarting its agent. Fail-open has a direction: an unreachable
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
  Code itself. The addon may swap the auth header and drop/refuse connections;
  it must never rewrite a request body — the fingerprint has to stay Claude
  Code's own, and the account at risk is a person's.
- **Attribution from claims, not headers** (#198): with `CHEESE_SCOPED_SECRET`
  set, project/topic come from the verified scoped token the sandbox carries as
  its Bearer. `CHEESE_ALLOW_HEADER_ATTR=1` re-enables the legacy spoofable
  `x-cheese-attr` header and is acceptable ONLY while the proxy is reachable
  solely on the box's own docker bridge.
- **The CONNECT gate fails closed**: no `CHEESE_SCOPED_SECRET` means every
  CONNECT is refused (407), not relayed on trust. Now that the bind address is a
  per-box setting, the dangerous configuration — widened bind, secret forgotten
  — has nothing about it that looks like a failure, so it must not be one that
  quietly works. `CHEESE_ALLOW_HEADER_ATTR=1` stays the one explicit opt-out.
- **Fail-open admission**: an unreachable backend logs and allows. A brake that
  can take the platform down is worse than the overspend it prevents; the token
  cap stays as the deployment-wide backstop.

## Cutover from the hand-managed ~/cheese-proxy (dev box)

1. Stop nothing yet. Copy the box-local values into `deploy/metering-proxy/.env`:
   upstream auth + via, `CHEESE_SCOPED_SECRET` = backend's `SANDBOX_TOKEN`,
   `CHEESE_ADMISSION_URL` pointing at the backend on the bridge, and
   `USAGE_LOG_DIR` = the directory the backend's `SUBSCRIPTION_USAGE_LOG` is in.

   Both of those last two fail silently when wrong, which is why they are named
   here rather than left to the compose defaults. An unset `CHEESE_ADMISSION_URL`
   disables per-project budgets *and* leaves every enrolled machine unplaceable
   on its own ccproxy identity. A `USAGE_LOG_DIR` that is not the backend's
   ingest directory meters into a file nobody reads: rows are written, the
   ledger never grows, and neither side reports anything. Both were wrong on
   dev after the cutover below, and neither was noticed until a turn was traced
   end to end (2026-08-15).
2. Point `INJECT_SECRETS_DIR` at the host directory that CONTAINS `inject.token`
   (mounted read-only at `/etc/cheese/secrets`, a directory — not a single-file
   bind mount — so an atomic token swap is seen without a restart; see below),
   and `CERTS_DIR` at the CA dir — the CA is baked into sandbox images'
   `NODE_EXTRA_CA_CERTS` by absolute path, so the cert must not change identity.
3. `docker compose -f deploy/metering-proxy/compose.yml up -d` after
   `docker rm -f cheese-metering-proxy` (same name, same published address —
   in-flight turns see one connection reset, Claude Code retries).
4. Verify: a sandbox turn answers; `logs/usage.jsonl` gains rows whose
   project/topic match the turn; with a zero-grant test project,
   `/v1/messages` is refused 429 with the budget reason.
5. Retire the old `~/cheese-proxy/addons` copy. The injector holds a durable,
   non-refreshing credential (see "The injected credential" below), so there is
   no creds daemon or token-refresh loop to keep running — retire those too, and
   mask any leftover unit so it cannot start by accident.

## The injected credential

The token in `inject.token` is a durable, **non-refreshing** credential: a
one-year setup-token minted by `claude setup-token` (Anthropic's
service/automation credential), or the stable fake token an m161/ccproxy
setup-token-backed machine hands back. It is NOT a Claude Code interactive
`/login` access token and NOT sourced from Claude Code's login credential JSON:
those age out in hours and only stay alive via an OAuth refresh chain, and
treating that human-session credential as a service credential — with a local
"refresh near expiry" daemon owning the file — is the exact failure that caused
an outage. So there is **no refresh loop, no daemon, no print-mode refresh
call** anywhere in this deployment; rotation is a planned, roughly annual, manual
swap. A CI guard (`.claude/scripts/check-metering-proxy.sh`) fails the build if
any of those retired mechanisms reappear under `deploy/`.

- **Absent/empty injector = fail closed.** With no token the proxy returns a
  local `503` before forwarding, rather than sending the sandbox's scoped bearer
  upstream to collect an opaque `401`. That 503 means "the platform's
  setup-token is missing or expired — (re)install it on the host."
- **Atomic rotation, no restart.** `inject.token` lives in a directory mounted
  read-only at `/etc/cheese/secrets`. Because a directory (not the single file)
  is bind-mounted, a rotation done as write-new-then-rename is resolved on the
  addon's next per-request read with no container restart and no inode trap.
  **Changing this mount requires a one-time metering-proxy rebuild** on the box
  (via the normal deploy flow — `docker rm -f cheese-metering-proxy` then bring
  it back up); pre-existing single-file `inject.token` mounts keep working until
  that rebuild.
