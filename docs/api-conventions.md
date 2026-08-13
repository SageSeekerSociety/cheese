# Addressing the API

The URL you have to send is not the path you will find in the route file. This
page is the authority on the difference. If you are writing a client — a skill, an
agent, a script, another service — read the first section and you are done.

> **This describes a state being dismantled, not a convention to build on** (#370).
> The doubled `/api/api` exists only because the two generations owned the same
> resource words; each rename that frees a word retires part of it, and when the
> last one is free the 2.0 prefix goes and every URL becomes `/api/<resource>`.
> Landed so far: `project` (1.0 → `/team-projects`), `topic` (1.0 → `/tags`),
> `notification` (2.0 → `/alerts`). Left: `tasks`, which merges rather than
> renames. Read this page to address the API **today**; do not read it as a
> reason to keep the seam.

## The rule

**The backend is mounted at `/api` on the app's own origin. Append the backend
path to that base, verbatim.**

```
https://<app-host>/api  +  <path as the backend declares it>
```

That is the whole convention, and it is why two different-looking shapes are both
correct:

| Backend path | The URL you send |
|---|---|
| `/users/auth/login` | `https://<host>/api/users/auth/login` |
| `/api/topics/42` | `https://<host>/api/api/topics/42` |
| `/connector/my/devices` | `https://<host>/api/connector/my/devices` |
| `/healthz` | `https://<host>/api/healthz` |

The doubled `/api/api` is not a typo and not a bug to route around. One `/api` is
the mount point; the other belongs to the route itself.

You do not have to take this on trust or keep the table in your head. **The
published schema carries the base**: `GET https://<host>/api/openapi.json` returns
a `servers` list whose first entry is `/api`. Any OpenAPI client composes
`servers[0].url + path` and gets a working URL with no special knowledge. Swagger
UI at `https://<host>/api/docs` does the same.

## Why there are two shapes

The fused product carries two generations of routers:

- **知是 1.0** routers are bare — `/users`, `/questions`, `/spaces`, `/teams`.
- **知是 2.0 (CheeseX)** routers declare their own `/api` prefix — `/api/topics`,
  `/api/projects`, `/api/blocks`.

Nothing at the deployment layer *adds* a prefix. The frontend image owns the
public origin and forwards the API with one nginx block:

```nginx
location /api/ { proxy_pass http://backend:8081/; }
```

The trailing slash on `proxy_pass` is the entire mechanism: nginx replaces the
matched `/api` with `/`, so exactly one segment is stripped. A caller therefore
has to *pre-pay* that strip. For a 1.0 route the one `/api` you send is consumed
by the gateway and the bare path arrives. For a 2.0 route the `/api` you send is
consumed and the route's own `/api` arrives — so you had to send two.

`frontend/vite.config.ts` reproduces this for the dev server and
`e2e/playwright.config.ts` for the e2e run, so all three environments address the
API identically.

### The exception, and why it exists

Two path families reach the backend **un-stripped**, both because a browser
resolves their sub-resources against `location.pathname` and the backend must
therefore serve them at the exact path the browser asked for:

- `/api/topics/<id>/terminal…` and `/api/topics/<id>/app…` — matched by a regex
  `location` block that passes the URI through as-is.
- `/connector/…` — its own `location`, forwarded without a strip.

Neither breaks the rule above: those routes are also reachable the ordinary way
(`/api/api/topics/<id>/terminal`, `/api/connector/…`), because the generic `/api/`
block still matches. The exceptions add addresses; they remove none.

## Why the double prefix is still here

It looks like something to tidy up, and it will be — but not by deleting it.
While any resource word is owned twice, the prefix is the only thing keeping the
two owners apart.

The two generations both owned resources named `topics`, `projects` and `tasks`.
Collapsing them into a single external `/api/` namespace makes such a name
ambiguous, and FastAPI resolves the ambiguity silently — first router registered
wins, the other's endpoints simply stop existing. Measured on `main` 2026-08-12,
before the renames: of the 135 paths under the 2.0 prefix, dropping one `/api`
layer sent 128 to a 404 and 7 onto a live 1.0 route — ten endpoints at
method+path granularity, spread over all three words.

#370 is retiring that list one word at a time. What is left today:

| 2.0 endpoint | Single-prefixed, it reaches |
|---|---|
| `GET /api/tasks/{id}` | 1.0 `GET /tasks/{id}` |

`topics` and `projects` are gone from it: 1.0's tag moved to `/tags` and its team
project to `/team-projects`, so their bare forms now 404 rather than answering.
`tasks` will not leave by renaming — the two are the same resource written twice
and have to merge.

The table is not maintained by hand. `tests/contract/test_api_addressing_contract.py`
recomputes the set on every run and pins it, in both directions: a NEW collision
fails, and so does a pinned one that quietly disappears — because "the 1.0 route
was renamed" and "a 2.0 prefix was flattened" look identical from here, and only
one of them is good news.

This is not a thought experiment — it is the outage that produced the current
shape. `frontend/src/api.ts` records it: with a single `/api`, creating a project
answered 400, the project list 400'd, and `/api/topics` returned **200 from 1.0's
question tags**. A wrong answer with a success code is worse than a 404, and it is
what flattening too early would reintroduce.

So the order is fixed: free the words first, drop the prefix second. Until then
the seam is spelled in exactly two places — `BASE` in `frontend/src/api.ts` for
the browser, and the `servers` entry in `backend/app/main.py` for everyone else,
which is also the whole of what step 2 has to change.

### A trailing slash used to re-open the same trap (closed)

The strip happens once per pass through nginx — and a trailing slash used to buy
a second pass. Sending `GET <origin>/api/api/tasks/7/` (one stray `/`) went, as
verified live:

1. nginx strips one segment → the backend receives `/api/tasks/7/`.
2. No route matches; Starlette's slash-redirect answers **307** with
   `Location: <origin>/api/tasks/7` — an origin-absolute URL that has already
   spent its strip.
3. The client follows, nginx strips again, the backend receives `/tasks/7` —
   and a **1.0** route answers.

One character turned a correct 2.0 URL into a wrong-generation answer with a
success code. The backend cannot repair the `Location`, because it cannot know
how many prefixes the proxy ahead of it will strip — so the redirect itself had
to go: `app.main` now builds the app with `redirect_slashes=False`, and a
trailing slash is a plain 404. `test_a_trailing_slash_never_reaches_the_other_generation`
pins it, and fails if the behaviour is ever turned back on.

Still: **never send a trailing slash.** It is now an honest error instead of a
silent one.

## Notes for client authors

- **Point the base at the app origin, not at the backend port.** Deriving a base
  from a route file gives you a path nothing serves.
- **A base that must reach the backend root ends in `/api`.**
  `settings.connector_public_base` already carries this requirement: an enrolled
  device POSTs hooks to `{base}/sandbox/hooks/…`, and with the `/api` missing that
  lands on the SPA, which answers 200 and drops every agent event. That failure
  cost a day on dev (2026-08-08) — the machine worked, the platform saw nothing.
- **A 200 does not prove you reached the right endpoint.** Anything unmatched
  falls through to `location /`, which serves `index.html`. If a response body is
  HTML, your path never reached the backend.
- **Static connector artifacts live at the origin root**, not under `/api` —
  `<origin>/connector/latest/<target>/cheesehost`.

`backend/tests/contract/test_api_addressing_contract.py` pins all of this, from
three directions: it drives the real app at the URLs the schema advertises; it
recomputes the collision table above and fails if a 2.0 prefix is ever
flattened (or a new cross-generation collision is born); and it fails if nginx
stops stripping the segment the schema assumes. The schema alone cannot carry
that weight — it is generated from the routes and agrees with them by
construction — which is why the collision set and the 2.0 module list are
pinned in the test rather than derived.
