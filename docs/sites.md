# Project sites

The project navigation contains **导出与发布**. Project owners, project leads and
team administrators can publish; project and team members can open the published
Site. Its access does not become public when it is published.

## Publishing

Select a website from the project's accepted revision, then publish it. Each
candidate is a directory containing a tracked `index.html`. Its tracked resources
are copied into a release snapshot. Uncommitted files and unaccepted topic branches
are excluded. If the accepted revision changed since the page loaded, refresh and
review the new revision before publishing again.

The stable entry is `/sites/<project-id>` on the platform. It signs in the viewer
and opens the current release on that project's content origin. Accepting further
changes does not update the published website. A failed publish leaves the previous
release active. Opening and reloading a Site does not require the development
machine or the original Git checkout.

Publishing changes the version used by subsequent file requests. A tab already
running the previous version may need a refresh if its delayed scripts or assets
changed in the new release.

This implementation hosts static output. It does not run build scripts or host
application backends. A React/Vite source directory must be built and its static
output accepted before publication. All local resources must be inside the selected
directory. The publication limit is 2,000 files and 100 MiB, with a 1 MiB HTML entry.
Browser storage stays local to each viewer and is preserved across publications;
Sites does not add a shared database or cross-device synchronization.

## Deployment

The existing persistent workspace volume also holds `.sites/<project>/<release>`.
Keep this directory when updating containers, and include it when backing up the
workspace volume alongside the database. Do not expose it through `/uploads` or a
static-file alias.

Leave `SITES_DOMAIN` empty until the content domain is ready. The delivery page
will display that hosting is unavailable. Activation requires:

1. A dedicated content domain outside the platform's registrable domain, with
   wildcard DNS and TLS. For example, a platform at `app.example.com` could use
   `SITES_DOMAIN=example.net` for project hosts at `<uuid>.example.net`.
2. A gateway forwarding every path on `*.example.net` to the backend while
   preserving the original `Host`. Do not add or strip `/api` on this route. The
   backend claims the entire content host before platform API routing.
3. `FRONTEND_URL` set to the platform's browser origin and `SITES_SCHEME=https`.
   Leave `SITES_PORT` unset for standard HTTPS. Never add content domains to
   credentialed CORS or place platform cookies on a parent content domain.

The domain check conservatively rejects domains sharing their final two labels.
For multi-label public suffixes, use a content domain with a different suffix.

Cloudflare Universal SSL covers the root domain and first-level subdomains on a
full DNS setup. A nested choice such as `SITES_DOMAIN=sites.example.net` requires
additional certificate coverage for `<uuid>.sites.example.net`.
See [Cloudflare's certificate coverage documentation](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).

### Existing api-front deployments

Prepare the optional host route in the same `ACTIVE_BACKEND_DIR` used by `up.sh`
and deployment:

```sh
bash deploy/llm-tunnel/configure-sites.sh example.net /path/to/active
```

This writes `sites.conf` without reloading nginx. Review the generated file and
ensure api-front uses the updated `nginx.conf`, which includes the optional file.
On first installation, recreate api-front with
`bash deploy/llm-tunnel/up.sh --force-recreate api-front` so its file bind mount
uses the updated configuration. This briefly interrupts traffic through api-front.
For later domain changes, validate and reload:

```sh
docker exec cheese-api-front nginx -t
docker exec cheese-api-front nginx -s reload
```

The content server forwards every path, including `/llm/tunnel`, to `backend_active`
with the original `Host`. The platform server retains its existing route to the
tunnel terminator. Use `configure-sites.sh --disable /path/to/active` to prepare
removal, then validate and reload again.

Point the content wildcard's Cloudflare Tunnel ingress at `http://localhost:8081`
on the existing application host. Keep its `Host` header and full request path.
Route this hostname through the content server instead of the frontend container.

### Local verification

Local browser tests may use `SITES_DOMAIN=sites.localhost`, `SITES_SCHEME=http`
and `SITES_PORT=<backend-port>`. Bind these test services to loopback.

Run `python3 deploy/llm-tunnel/verify-sites.py /path/to/nginx` on Mac mini before
activation. The test uses temporary loopback listeners and verifies platform
routing, content routing, and disabling the optional host rule.

Before activation, verify a private sample through the actual gateway: an eligible
viewer can load its HTML, module scripts and images; a different project cannot use
the grant; content-host requests to platform APIs fail; removing membership blocks
subsequent reads. Site grants travel in POST bodies and expire after 30 seconds.
Content sessions last eight hours and are scoped to one project and viewer.

Private responses are not cached, and Service Workers are disabled. Membership
removal prevents future server reads; it cannot remove content already downloaded
by a viewer. Ordinary browser storage is available on the project's content origin.
