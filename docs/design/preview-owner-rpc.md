# Preview owner role and RPC1

This is the independent preview owner. `PREVIEW_CONNECTION_MODE` defaults to
`owner` in `deploy/preview-connection.env` (the repo-level kill switch: set it to
`legacy` to route every preview back onto the business backend on the next
deploy). Public routing, cookies, resource identity and helper wire formats keep
their existing behavior in both modes. This change adds the deployment slice: a
`preview-connection` compose service, the api-front routes for both tunnel
spellings and the preview content hosts, a separately released owner with no
drain, and the kill switch.

The independent entry is `app.preview_connection_app:create_app` with Uvicorn
`--factory --workers 1`. It has its own hub, incarnation and lifespan, mounts
the whole existing preview-host policy, both `/preview/tunnel` and
`/api/preview/tunnel`, and the private RPC. It does not discover business routers
or import `app.main`, work runners or scheduling startup. Existing DB schema,
stable JWT signing configuration, library storage and content file mounts must
be shared with the business backend when it is eventually deployed.

In explicit `owner` mode, the business backend requires a service origin in
`PREVIEW_CONNECTION_URL`; metadata, fixed app selection and shown admission use
that service. Tunnel admission is not mounted on the business app. A misrouted
preview-host request fails closed instead of using its empty local hub. Existing
authorization, latest-artifact checks and resource fields stay in the business
backend. Owner RPC failure does not trigger a fallback, repeat shown mutation,
application request or WebSocket send.

## RPC1

`POST /_internal/preview/v1/inspect` accepts only `topic_id`, `seat`, optional
64-hex `expected_instance`, `wait_ms` (0–8000), and `probe`. It cannot select an
arbitrary URL, path, port, body or mutate an artifact. Authentication is a
30-second HS256 service token, with audience `cheesex:preview-owner:inspect:v1`
and subject `preview-backend`. The signing context is derived from the existing
JWT root with `cheesex:preview-owner-rpc:v1:`; user/session grants are not service
credentials. The route is outside business router discovery. Future ingress
must keep this namespace private and bind the owner port only on a private
service interface.

Requests are capped at 1024 bytes, with a one-second body deadline and 64 active
reads. Inspection uses a total budget of `wait_ms + 5000ms`; the backend client
adds one second for transport and caps responses at 4096 bytes. Client redirects
and automatic retries are disabled. Cancellation/disconnect cancels inspection
and its original stream. One backend lifespan owns one HTTP connection pool.

Responses carry protocol 1, owner incarnation, captured transport epoch,
capabilities and state: `online`, `transport_unavailable`, `app_unavailable`,
`instance_gone`, or `instance_identity_unsupported`. Incarnation and epoch do
not enter the artifact, native listener instance, cookie audience or resource
key. An old helper can still pass shown liveness without native identity support;
it cannot authorize a fixed instance selection. Instance inspection and guarded
probe remain on one captured machine, and a displaced result is discarded.

Owner content relay uses typed admission failures: a definitive native instance
replacement is 409; unavailable transport/application admission is 503 with
`Retry-After: 1` and `Cache-Control: no-store`. Actual app response status and
headers still pass through. Legacy admission status stays unchanged. No request
is transferred or replayed after failure. Shutdown closes admission, wakes
waiters and terminates original streams; planned close uses ordinary retryable
loss/1012 and never helper-superseded 4001. Existing equal-iat redial, older-live
winner refusal and identity-checked detach remain in force.

## Ingress cutover and what remains

The cutover is now implemented. `deploy/deploy-docker.sh`'s
`ensure_preview_connection_owner` starts the owner (when `owner` mode and it is
absent) and waits for `/healthz` **before** api-front installs the tunnel route
or the content-host split and **before** any backend is replaced in owner mode;
a failure there aborts the deploy with no route or backend touched. api-front
routes `^/(api/)?preview/tunnel$` to the owner and splits preview content hosts
inside the box-generated `*.DOMAIN` wildcard server (`active/sites.conf`), since
no separate `server_name` can beat that wildcard. The owner's internal RPC
namespace stays reachable only inside the compose network. The
`preview-connection` owner is a normal app deploy's untouched peer (started only
if absent) and is released only through `deploy/release-preview-connection.sh` /
**Release preview connection owner**, which recreate it with no drain and so
drop every live tunnel (helpers redial). `PREVIEW_CONNECTION_MODE=legacy` in
`deploy/preview-connection.env` routes all of it back onto the business backend
on the next deploy, so a revert needs no box access.

What still is not claimed here: real production/WAN acceptance, more than one
owner replica or Uvicorn worker (unsupported), durable highest-issued-credential
fencing across an owner restart (the preserved newest-credential fence is
live-process only), and browser/heap continuity beyond the ingress tests.
Storage/DB compatibility is exercised by the shared env file and mounts, not by
a staged production migration.

The real socket regression starts an independent ASGI owner process, an actual
scoped helper socket/Session and a native HTTP app, then replaces the backend
client lifecycle and checks the owner incarnation and transport epoch persist.
The helper runs in a test thread and deliberately offers legacy capabilities;
this is not Linux native-identity, real backend-process rollout, DB authorization,
nginx >32-second worker retirement, browser heap continuity or production/WAN
acceptance. Existing integration fixtures cover the three authorized business
readers, with the remote inspection substituted. Full-host authentication/file
policies remain owned by their existing integration suites. Normal Linux CI is
required; a Windows run without the repository conftest is isolated evidence,
not a passing normal pytest suite.
