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
- `compose.yml` — the container. Values via a box-local `.env` (see header).

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
  cap stays as the deployment-wide backstop.

## Release on the dev box

The dev application deployment releases the metering image after updating the
application, using the same full commit SHA and requiring its image build and
Required CI to succeed. A healthy proxy already running that digest is left
running, so an unchanged image does not interrupt active streams.

Use the **Release metering proxy** GitHub Actions workflow from `main`, with the
full merged commit SHA. Its image build and Required CI must both have succeeded
for that exact SHA. Acknowledge the interruption only when ready to recreate the
proxy: active model streams can disconnect.

The build pins mitmproxy 12.1.2 and packages the addon, billing core and control
answers into the image. The release pulls the full-SHA tag, resolves its registry
digest and starts that digest. No host source directory is mounted over `/addons`.

The existing box-local `.env` remains at
`~/cheese-proxy-new/deploy/metering-proxy/.env`. The release preserves its project
directory, published ports, ledger and CA mounts.
`CHEESE_ADMISSION_URL` must point at the backend's `/llm/admission`;
`USAGE_LOG_DIR` must match the backend's `SUBSCRIPTION_USAGE_LOG` directory.
`CERTS_DIR` holds the existing CA.
Do not print the environment or replace the CA during a release.

Before recreating the service, the release saves its image ID and raw compose
files in a private `releases/<sha>-<run>-<attempt>/` directory. It leaves legacy
source files in place for the first release's rollback. Failed health checks
restore the previous image and mounts. Health checks require both listeners and
an unauthenticated CONNECT response of 407; they do not call a model provider.

After release, verify a sandbox turn, a usage row attributed to that turn, and a
budget refusal for a test project whose compute grant is exhausted. Listener health alone does not
verify that the platform's credential reaches Anthropic, backend admission or
ledger ingestion.

## The Claude credential

The proxy holds the platform's only Claude credential, in
`<proxy home>/claude-credential/credential`, and no session holds any: every
Claude Code session boots on `NO_LOGIN_PLACEHOLDER` (`cheese_billing_core.py`),
which authenticates nothing. On a request bound for Anthropic the proxy puts
the credential in place of the placeholder; on one admission routes to the
gateway it puts the project's virtual key.

The file is re-read on every request, so logging in, switching account and
logging out take effect at the next request of every running session. Use
`claude-login.sh` on the box, as the directory's owner:

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

With no credential, projects on the API-key pool still run. The boot calls only
a real account can answer (`NO_LOGIN_ANSWERS`) are answered here. A request
admission places on the subscription is refused with a 400 naming the missing
login, which the client does not retry; one admission could not place gets a
503 and is retried.
