# Addressing the API

The URL you have to send is not the path you will find in the route file. This
page is the authority on the difference — one prefix's worth, and no exceptions
any more. If you are writing a client (a skill, an agent, a script, another
service), read the first section and you are done.

## The rule

**The backend is mounted at `/api` on the app's own origin. Append the backend
path to that base, verbatim.**

```
https://<app-host>/api  +  <path as the backend declares it>
```

Every route is bare, so every URL has exactly one `/api`:

| Backend path | The URL you send |
|---|---|
| `/users/auth/login` | `https://<host>/api/users/auth/login` |
| `/topics/42` | `https://<host>/api/topics/42` |
| `/connector/my/devices` | `https://<host>/api/connector/my/devices` |
| `/healthz` | `https://<host>/api/healthz` |

You do not have to take this on trust. **The published schema carries the base**:
`GET https://<host>/api/openapi.json` returns a `servers` list whose first entry
is `/api`, so any OpenAPI client composes `servers[0].url + path` and gets a
working URL with no special knowledge. Swagger UI at `https://<host>/api/docs`
does the same.

Nothing at the deployment layer *adds* a prefix. The frontend image owns the
public origin and forwards the API with one nginx block:

```nginx
location /api/ { proxy_pass http://backend:8081/; }
```

The trailing slash on `proxy_pass` is the entire mechanism: nginx replaces the
matched `/api` with `/`, so exactly one segment is stripped and the caller
pre-pays that strip. `frontend/vite.config.ts` reproduces it for the dev server
and `e2e/playwright.config.ts` for the e2e run, so all three environments address
the API identically.

## The one thing that still bites: browser path ≠ route path

The strip is why a backend route is bare while the URL the browser actually
visited begins with `/api`. Almost nothing needs to care — but anything the
backend hands back **for the browser to resolve against** does, and it cannot
read it off the request:

- a proxy cookie's `Path`,
- a URL rewritten into proxied HTML,
- a `url` field the frontend will put in an iframe.

Build those with `app.api.proxy.browser_path()`, never from `request.url.path` —
that is the stripped one, and a cookie scoped to it is silently never sent. Two
places need this today, the 施工现场 terminal and the app preview
(`app/api/routes/terminal.py`, `app_preview.py`).

This is a smaller trap than the one it replaced, but it is sharper: it fails only
in a browser, behind the gateway, and never in a test that talks to the backend
directly.

## Never send a trailing slash, and never turn the redirect back on

`app.main` builds the app with `redirect_slashes=False`, and that must stay off.

Not because the prefix is unknowable — it is a constant, `proxy.GATEWAY_MOUNT`,
and `browser_path()` is one line built on it. The problem is that **Starlette's
slash-redirect never calls our code**. It builds an origin-absolute `Location`
out of the path *it* was handed, which is the stripped one, so the browser is
sent to `<origin>/topics/42` — a URL with no `/api`, which the gateway does not
route to the backend at all.

Repairing that would mean a middleware rewriting the framework's 307s. Not worth
writing, for a request that should not be made: a trailing slash has no correct
meaning here. `test_a_trailing_slash_is_never_a_redirect` pins the setting.

For a client, the consequence is one line: **a trailing slash is a plain 404**,
which is an honest error instead of a silent wrong answer. The history of how
that was discovered is below and is only history.

## Why there used to be two shapes

Until #370 the product carried two generations of routers: 知是 1.0 bare
(`/users`, `/questions`, `/spaces`, `/teams`) and 知是 2.0 (CheeseX) declaring
their own `/api` (`/api/topics`, `/api/projects`, `/api/blocks`). A 2.0 URL
therefore needed **two** — `https://<host>/api/api/topics/42` — one for the mount
and one for the route.

That doubling was not decoration; it was the only thing keeping the two owners of
a duplicated resource word apart. Both generations owned `topics`, `projects` and
`tasks`, and FastAPI resolves such an ambiguity silently — first router
registered wins, the other's endpoints simply stop existing. Measured on `main`
2026-08-12: of the 135 paths under the 2.0 prefix, dropping one `/api` layer sent
128 to a 404 and 7 onto a live 1.0 route. `frontend/src/api.ts` recorded what
that felt like — creating a project answered 400, the project list 400'd, and
`/api/topics` returned **200 from 1.0's question tags**. A wrong answer with a
success code is worse than a 404.

So the order was fixed and it was followed: free every duplicated word first
(`project` → `/team-projects`, `topic` → `/tags`, `notification` → `/alerts`,
`task` merged rather than renamed), then drop the prefix. The collision count
went 10 → 4 → 1 → 0, pinned at each step by
`tests/contract/test_api_addressing_contract.py`, which failed both on a new
collision and on a prefix flattened too early.

Two nginx/vite exceptions went with it. The terminal and app-preview prefixes
used to be forwarded **un-stripped**, because those routes carried their own
`/api` and a strip would have 404'd them; now that every route is bare they are
ordinary traffic, and the special-casing is deleted rather than kept "just in
case". `/connector/…` keeps its own `location` block for an unrelated reason —
enrolled devices dial it directly.

### How the trailing-slash trap was found (history)

The strip happens once per pass through nginx — and a trailing slash used to buy
a second pass. Sending `GET <origin>/api/api/tasks/7/` (one stray `/`) went, as
verified live:

1. nginx strips one segment → the backend receives `/api/tasks/7/`.
2. No route matches; Starlette's slash-redirect answers **307** with
   `Location: <origin>/api/tasks/7` — an origin-absolute URL that has already
   spent its strip.
3. The client follows, nginx strips again, the backend receives `/tasks/7` —
   and a **1.0** route answered.

One character turned a correct 2.0 URL into a wrong-generation answer with a
success code. Starlette builds that `Location` itself, from the stripped path,
without consulting anything of ours — so the redirect itself had to go: `app.main` builds the app with `redirect_slashes=False`, and a trailing
slash is a plain 404. With one namespace there is no other generation left to
land on, but the redirect stays off and `test_a_trailing_slash_is_never_a_redirect`
still pins it: an origin-absolute `Location` from behind a stripping gateway is
wrong regardless of what it hits.

## Notes for client authors

- **Point the base at the app origin, not at the backend port.** Deriving a base
  from a route file gives you a path nothing serves.
- **A base that must reach the backend root ends in `/api`.**
  `settings.connector_public_base` already carries this requirement: an enrolled
  device POSTs hooks to `{base}/sandbox/hooks/…`, and with the `/api` missing that
  lands on the SPA, which answers 200 and drops every agent event. That failure
  cost a day on dev (2026-08-08) — the machine worked, the platform saw nothing.
- **The sandbox CLI's base is the opposite case**: `settings.sandbox_api_base` is
  handed to a tool that appends its own paths, so a stale `/api` suffix on it now
  double-counts. `settings.agent_api_base()` normalizes it away and the backend
  warns at boot (`sandbox_api_base_has_stale_api_suffix`) rather than letting a
  404 look like "the agent chose not to use its tools".
- **A 200 does not prove you reached the right endpoint.** Anything unmatched
  falls through to `location /`, which serves `index.html`. If a response body is
  HTML, your path never reached the backend.
- **Static connector artifacts live at the origin root**, not under `/api` —
  `<origin>/connector/latest/<target>/cheesehost`.

`backend/tests/contract/test_api_addressing_contract.py` pins all of this: that
one namespace holds one shape (no route carries the gateway prefix any more, and
the schema's advertised base still composes), that the real app answers at the
URLs the schema advertises, that nginx still strips the segment the schema
assumes, and that a trailing slash never becomes a redirect.
