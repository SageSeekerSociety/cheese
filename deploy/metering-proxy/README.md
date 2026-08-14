# Subscription metering proxy

The transparent proxy between a sandbox's Claude Code and Anthropic. It swaps
the sandbox's scoped cheese token for the real subscription credential (which
never leaves this proxy), meters every `/v1/messages` response into
`usage.jsonl` (ingested by the backend — `subscription_ingest`), and refuses a
turn its project cannot afford (backend `/llm/admission`, plus a rolling token
cap as backstop). Design context: issue #218 and
`docs/plans/2026-08-10-usage-unification-design.md`.

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
- **Fail-open admission**: an unreachable backend logs and allows. A brake that
  can take the platform down is worse than the overspend it prevents; the token
  cap stays as the deployment-wide backstop.

## Cutover from the hand-managed ~/cheese-proxy (dev box)

1. Stop nothing yet. Copy the box-local values into `deploy/metering-proxy/.env`:
   upstream auth + via, `CHEESE_SCOPED_SECRET` = backend's `SANDBOX_TOKEN`,
   `CHEESE_ADMISSION_URL` pointing at the backend on the bridge.
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
