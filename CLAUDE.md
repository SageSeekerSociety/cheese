# Project Conventions

## Python version
- Python version is managed by uv (pinned in `.python-version`). Currently >=3.11.
- Do NOT use `from __future__ import annotations`. The project targets Python 3.11+ where `list[str]`, `dict[int, str]`, `X | None` etc. work natively in annotations.
- Do NOT name methods `list`, `set`, `dict`, `type`, or other builtin names — they shadow builtins in class scope and break type annotations.
- Use quoted strings (`"PermissionRule"`) only for genuine forward references (e.g., self-referencing class in a `@staticmethod` return type).
