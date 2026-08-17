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

**Setting `root_path` does not fix this, so do not try it.** That is the obvious
next idea and it is a dead end: Starlette dropped `root_path` from slash
redirects in 0.35.0 (FastAPI 0.109), and the discussion asking for it back —
[starlette#2514](https://github.com/Kludex/starlette/discussions/2514) — is
still open. We run 1.3.x, well past that. The same machinery has a second known
defect, [#1396](https://github.com/Kludex/starlette/issues/1396): an https
request can be redirected to an http `Location`.

Repairing it properly would mean a middleware rewriting the framework's 307s.
Not worth writing, for a request that has no correct meaning here in the first
place. `test_a_trailing_slash_is_never_a_redirect` pins the setting.

For a client, the consequence is one line: **a trailing slash is a plain 404**,
which is an honest error instead of a silent wrong answer.

## Two routes must never answer the same URL

FastAPI resolves that ambiguity silently: the first router registered wins and
the other's endpoints simply stop existing — no warning, no error, just a URL
that quietly belongs to someone else. That is worse than a 404, because it can
answer with a success code.
`tests/contract/test_api_addressing_contract.py` fails on any pair that claims
the same URL, which is what makes a single flat namespace safe to keep.

`/connector/…` has its own nginx `location` block, because enrolled devices dial
it directly rather than through the app origin. Nothing else is special-cased.

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
