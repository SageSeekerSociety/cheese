# Addressing the API

The URL you have to send is not the path you will find in the route file. This
page is the authority on the difference. If you are writing a client — a skill, an
agent, a script, another service — read the first section and you are done.

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
UI at `https://<host>/api/docs` and ReDoc at `https://<host>/api/redoc` do the
same — both name the spec by a URL relative to themselves, so they resolve it to
`https://<host>/api/openapi.json` rather than to the origin root, where nginx
would hand back the SPA's `index.html` with a 200 on it.

`cheese api` is the schema consumer inside this repo: it builds its command tree
from `/openapi.json` at every run, and Restish — the library behind it — takes the
first `servers[]` entry starting with `/` as the base path for every operation
URL. So the entry above is what makes `cheese api list-topics` send
`<origin>/api/api/topics`. With no entry published, Restish falls back to the path
of whatever base the machine was configured with, which is how an enrolled machine
(`cheesehost auth login <origin>/connector`) sent every call to `/connector/…`.
`cli/internal/apicli/apicli_test.go` pins this for the three bases that are
actually handed out.

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

## Why the double prefix is not being flattened

It looks like something to tidy up. It is load-bearing.

The two generations both own resources named `topics`, `projects`, `tasks` and
`spaces`. Collapsing them into a single external `/api/` namespace makes those
names ambiguous, and FastAPI resolves the ambiguity silently — first router
registered wins, the other's endpoints simply stop existing. Most of the 2.0
surface would simply 404, which is survivable; these ten endpoints would instead
answer, from the wrong generation:

| 2.0 endpoint | Single-prefixed, it reaches |
|---|---|
| `GET /api/topics` | 1.0 `GET /topics` |
| `POST /api/topics` | 1.0 `POST /topics` |
| `GET /api/topics/{id}` | 1.0 `GET /topics/{id}` |
| `GET /api/projects` | 1.0 `GET /projects` |
| `POST /api/projects` | 1.0 `POST /projects` |
| `GET /api/projects/{id}` | 1.0 `GET /projects/{projectId}` |
| `GET /api/projects/{id}/members` | 1.0 `GET /projects/{projectId}/members` |
| `POST /api/projects/{id}/members` | 1.0 `POST /projects/{projectId}/members` |
| `DELETE /api/projects/{id}/members/{handle}` | 1.0 `DELETE /projects/{projectId}/members/{userId}` |
| `GET /api/tasks/{id}` | 1.0 `GET /tasks/{taskId}` |

That table is not maintained by hand: `test_flattening_the_mount_would_cross_the_two_generations`
recomputes it from the live app and fails when it moves, in either direction — an
entry leaving means a 2.0 prefix was flattened, an entry arriving means a new
route landed on a name the other generation already owns.

This is not a thought experiment — it is the outage that produced the current
shape. `frontend/src/api.ts` records it: with a single `/api`, creating a project
answered 400, the project list 400'd, and `/api/topics` returned **200 from 1.0's
question tags**. A wrong answer with a success code is worse than a 404, and it is
what a flattening would reintroduce for those ten.

So the seam stays where it is until the 1.0 surface is retired, and it is spelled
in exactly two places — `BASE` in `frontend/src/api.ts` for the browser, and the
`servers` entry in `backend/app/main.py` for everyone else.

### A trailing slash still leaks across the seam

One route into the wrong generation survives all of the above, and it is worth
knowing about because it looks like a redirect being helpful. Send
`<origin>/api/api/topics/` — the correct 2.0 URL with a trailing slash — and:

1. nginx strips one segment, so the backend sees `/api/topics/`;
2. Starlette redirects to the slash-less form with an **origin-absolute**
   `Location: <origin>/api/topics`;
3. the client follows it, nginx strips again, and the backend sees `/topics` —
   the 1.0 route, which answers 401 without a credential and 1.0 data with one.

The redirect is built from the path the backend saw, which has already lost the
mount, so a second strip is applied to a URL that was only ever supposed to get
one. Nothing in the app can rewrite it correctly without knowing the mount at
request time. **Do not send a trailing slash on a 2.0 URL**; the published paths
never carry one.

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
- **An OpenAPI client pointed straight at the backend port sends one `/api` too
  many.** The published server assumes a gateway in front, and a generated client
  cannot know there isn't one; the second `servers[]` entry (`/`) describes that
  case but nothing picks it automatically. Debugging against `:8081`, drop the
  mount by hand.

`backend/tests/contract/test_api_addressing_contract.py` pins all of this: it
drives the real app at the URLs the schema advertises, it fails if a 2.0 router
leaves the mount, and it fails if nginx stops stripping the segment the schema
assumes. `cli/internal/apicli/apicli_test.go` pins the same contract on the
consumer side.
