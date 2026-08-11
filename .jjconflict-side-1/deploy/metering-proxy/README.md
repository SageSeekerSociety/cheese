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
2. Point `INJECT_TOKEN_FILE` and `CERTS_DIR` at the EXISTING token file and CA
   dir — the CA is baked into sandbox images' `NODE_EXTRA_CA_CERTS` by absolute
   path, so the cert must not change identity.
3. `docker compose -f deploy/metering-proxy/compose.yml up -d` after
   `docker rm -f cheese-metering-proxy` (same name, same published address —
   in-flight turns see one connection reset, Claude Code retries).
4. Verify: a sandbox turn answers; `logs/usage.jsonl` gains rows whose
   project/topic match the turn; with a zero-grant test project,
   `/v1/messages` is refused 429 with the budget reason.
5. Retire the old `~/cheese-proxy/addons` copy (leave the creds daemon and
   token-refresh loop untouched — they own the inject token file, not this).
