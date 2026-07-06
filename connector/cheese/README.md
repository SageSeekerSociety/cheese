# cheese CLI

A thin CLI over the Cheese backend. It embeds [Restish](https://rest.sh) as a
library, points it at the backend's **live OpenAPI**, and builds its command tree
from the spec on every run. When the server's API changes, the CLI changes with
it — no rebuild, no reinstall, no manual update step (like a web page whose
content changed). The client carries **zero business logic**; all business rules
live in the backend.

An agent uses this CLI exactly as a human uses the web UI: the same endpoints,
authenticated with the agent's session token (an agent *is* a user).

## Build

```sh
cd connector/cheese
go build -o cheese .
```

## Configure (env)

| Var                | Default                  | Meaning                                             |
|--------------------|--------------------------|-----------------------------------------------------|
| `CHEESE_API`       | `http://localhost:8080`  | Backend base URL.                                   |
| `CHEESE_TOKEN`     | —                        | Session token, sent as `Authorization: Bearer …`.   |
| `CHEESE_CONFIG_DIR`| `$XDG_CONFIG_HOME/cheese` | Where the generated `apis.json` is written.         |

## Use

```sh
cheese                       # list every operation (from the live OpenAPI)
cheese <operation> -h        # help for one operation, generated from the spec
cheese <operation> --flag v  # query/header params become --flags
cheese <operation> field: v  # request-body fields use `key: value` shorthand
```

Example:

```sh
export CHEESE_TOKEN=…
cheese create-conversation-ai-conversations-post title: "hello"
```
