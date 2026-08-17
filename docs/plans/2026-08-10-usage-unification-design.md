# Usage unification: one accounting model over two supply routes

Issue: #218. Status of record as of 2026-08-10, verified on the dev box (live
container env, gateway config, metering-proxy addon and its 12k-row usage log).

## The problem, restated as mechanism

A turn's model traffic takes one of two routes:

- **gateway** — LiteLLM, reached either directly (local sandbox env) or through
  the backend's `/llm` route (machines). Metered per project virtual key in the
  gateway's spend log. Priced in USD.
- **subscription** — the metering proxy (mitmproxy), which swaps in the real
  OAuth token and forwards via ccproxy. Metered per project/topic in the proxy's
  `usage.jsonl`. Tokens only, no USD price.

Whether a turn's spend reaches the platform's books is currently decided by
`SUBSCRIPTION_ENABLED` — a deployment flag — in `ChatService._model_kwargs`:
when true, **every** turn is treated as non-gateway, including device turns
whose traffic demonstrably flows through `/llm` → LiteLLM. Their spend sits in
the gateway's log and is never drained. Subscription turns' spend sits in the
proxy's log, which nothing reads. The transcript usage reader (#153) ships on
devices but its consumer (`usage_from_hook`) has no caller, and the spool
reconcile deletes its parked events. Net: `resource_usage` records zeros while
both logs fill with real numbers.

Enforcement is similarly dormant: gateway `max_budget` needs
`LLM_GATEWAY_CREDIT_USD` (unset), the proxy's `TOKEN_CAP` is 0, and
`budget_proxy.decide` has no caller.

## Design

One sentence: **the route a turn's traffic took is a per-turn fact, recorded on
the turn; each route has exactly one authoritative meter; both meters land in
`resource_usage` + `compute_grants`; each route has one enforcement point that
consults the same numbers.**

### Route is decided by the provider, not the deployment

`_model_kwargs` gains the executing provider's name. Routes:

| provider | condition | route | meter |
|---|---|---|---|
| device | always (machines require the gateway) | `gateway` | gateway spend-log drain |
| tmux | `subscription_enabled` | `subscription` | proxy usage.jsonl ingest |
| tmux / sdk | pool profile | `gateway` | gateway spend-log drain |
| sdk | native/testing profile | `native` | SDK-reported usage |

The subscription branch applies only to the provider that implements it (tmux).
The sdk provider under `subscription_enabled` previously fell through with no
env — inheriting the backend's own gateway credentials — and is now routed by
profile like before, closing that hole.

`resource_usage` gains a `route` column (`gateway|subscription|native|""`), so
every row names the supply that produced it.

Double-count rule (unchanged in spirit from the L1 note in `chat.py`): a route
with an authoritative meter never also bills provider-reported numbers. The
transcript reader has no authoritative role left on any route, so it is
deleted rather than wired (it never covered tmux; devices are gateway-metered).

### Subscription ingest (the missing half of the books)

A backend runner tails the proxy's `usage.jsonl` (read-only bind mount, path in
`SUBSCRIPTION_USAGE_LOG`), exactly-once via a byte-offset checkpoint persisted
in a new `ingest_state` table (offset + file fingerprint; a shrunk file resets
the offset). Each row lands as `resource_usage(kind="chat", route="subscription",
metered)` for its project/topic and consumes credits at the flat token rate
(`total_tokens`, all four buckets — matching the proxy's own cap arithmetic).
Runs on its own interval task (the scheduler ships disabled; nothing may hang
off it), started from the app lifespan when configured.

### Enforcement

- **gateway**: existing `max_budget` path; activated by setting
  `LLM_GATEWAY_CREDIT_USD` per deployment. With `COMPUTE_CREDIT_TOKENS=10000`
  and GLM at ~$3.94e-6/token, 1 credit ≈ $0.039 → `LLM_GATEWAY_CREDIT_USD=0.04`.
- **subscription**: the backend exposes a machine-facing admission route
  (`POST /llm/admission`, scoped-token authenticated) that answers
  `budget_proxy.decide` from the project's grant balance. The proxy calls it
  per `/v1/messages` (cached briefly), refusing before any bytes are forwarded.
  Fail-open with a loud log, same stance as every other admin dependency: a
  broken brake must not be a broken platform — the rolling `TOKEN_CAP` stays as
  the deployment-wide backstop.

### The proxy becomes versioned, backend-governed infrastructure

The addon currently lives unversioned in `~/cheese-proxy/addons/` on the box.
It moves into the repo (`deploy/metering-proxy/`), gaining:

- scoped-token verification (shared HMAC secret; attribution from verified
  claims, not the spoofable `x-cheese-attr` header) — the proxy half of #198;
- the admission call above;
- compose + runbook, wired to `SUBSCRIPTION_TOKEN_CAP`.

### local and device share a substrate, not a filesystem

With routes provider-decided and both meters unified, the remaining divergence
is transport (in-container tmux vs machine screen) and env assembly. They remain
separate isolation domains: local owns the backend topic workspace, while every
Device owns an independent checkout and syncs through git/file transfer. This
design removes the accounting reasons their shared substrate once differed.

## Delivery

| PR | contents | depends on |
|---|---|---|
| A | per-turn route + drain fix + `route` column + honest zero-usage rows | — |
| B | ingest runner + `ingest_state` + compose mount + reader deletion | A (route column) |
| C | admission endpoint (backend half) + credit-price activation notes | A |
| D | repo-ized addon: token verify, claims attribution, admission call, cap | C |
| — | PR #160 rebase + lint fix + merge (route reconcile on machines) | independent |

Deployment activation on dev, after merges: mount the proxy log into the
backend, set `SUBSCRIPTION_USAGE_LOG`, set `LLM_GATEWAY_CREDIT_USD`, roll the
proxy container onto the repo-ized addon with the shared secret. Prod is
unaffected until it opts in (no subscription, no proxy).
