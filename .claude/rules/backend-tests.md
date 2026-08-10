---
paths:
  - "backend/tests/**"
---

# Backend tests — auth helpers, fakes, and the shared DB

## Which auth mechanism to use (do NOT hand-roll)

Three exist; pick by directory. Eight files already carry a copy-pasted
`_auth()` helper — don't add a ninth.

- `tests/integration/`: the `auth_headers` fixture (integration/conftest.py) —
  real user + real login. For a raw token, `seed_user`.
- `tests/contract/`: the `authed_client` fixture (contract/conftest.py). Its
  docstring explains why it must NOT post `/users/auth/login` (no Redis in that
  harness).
- A bare session token (`mint_session_token`) is NOT a 知是 access token:
  `require_auth_user` rejects it. Routes behind it need the access-JWT shape
  (`{sub, type:"access"}`).

## When production code changes, its fakes change with it

Three CI breaks in one day came from stale test doubles: a fake `add()`
returning None after the real one's return value became load-bearing, and two
files still monkeypatching a function that had been renamed. Rule: whenever you
change a function's signature, return value, or module-level name, grep
`backend/tests/` for every fake/mock/monkeypatch of it and update them in the
same change.

## The test database is shared state

- Never run two pytest processes against the same test DB — they corrupt each
  other (10 phantom failures once). Serialize, or isolate databases.
- Tests changing auth/behavior on a route must update EVERY test file that
  calls that route (grep the path) — the accept-auth change missed one file and
  reddened main.
