"""The sandbox's own settings, in a module of their own.

`config.py` is at the size cap every module is held to
(`.claude/scripts/check-file-sizes.py`, `.claude/rules/architecture.md`), and
this section stands alone: a sandbox — the container a room runs an agent in —
reaching the backend, and the package sources it may fetch from. `Settings`
subclasses the class below, so the fields, their environment names and
`settings.sandbox_*` / `settings.package_mirror_*` are exactly what `config.py`
declared before the move: `backend/.env.example` is held to
`Settings.model_fields`, and a field declared anywhere on that class is a field
it reads.

What the mirror addresses mean — which mirror answers, when a mirror that does
not is left unset, and what a room already carrying one is told to drop — is
`app.domain.agent.package_mirrors`, applied as process environment in a room.
None of it reaches an agent's prompt, its skills or its memory.
"""

from pydantic import BaseModel


class SandboxSettings(BaseModel):
    # Base URL the in-container `cheese` CLI calls back to (host → backend).
    # The app ROOT, with no `/api`. The in-container `cheese` CLI reaches the
    # backend port DIRECTLY (no gateway, so nothing strips a prefix), and since
    # #370 step 2 the platform routes are bare — `{base}/projects/…`.
    #
    # A box whose .env still carries the old `…/api` value keeps working:
    # `agent_api_base()` strips one trailing `/api` and the boot warning names
    # the box so it can be cleaned up. Silently 404ing every `cheese` call would
    # look exactly like an agent that decided not to use its tools.
    sandbox_api_base: str = "http://host.docker.internal:8099"
    # Shared secret the sandbox `cheese` CLI sends (as its bearer credential) so the
    # cheese write-API isn't open on the bind address. Empty → derived from
    # `jwt_secret` (see `sandbox_signing_secret`); pin it to rotate the two
    # independently, or to share one secret across multiple backend hosts.
    sandbox_token: str = ""
    # RETIRED (2026-08-10). Used to name a HOST directory holding a `cheese` CLI
    # to mount over the image's baked copy — but nothing kept that checkout in
    # sync with the backend, so boxes served agents a months-old CLI. The CLI is
    # now staged per-topic from the backend's own copy (ws.session_dir →
    # bin/cheese), which is host-visible and fresh by construction. Kept only so
    # a box that still sets it gets the loud startup warning in main.lifespan
    # instead of silence; delete once no deployment sets it.
    sandbox_shim_host_dir: str = ""

    # Where a sandbox's `pip`, `uv`, `npm` and `playwright` fetch from. Only the
    # first two of those take a fallback: uv and pip are handed the domestic
    # index and then the official one, while npm and the browser host accept a
    # single URL each, so those are exported only where a probe says the mirror
    # answers (`app.domain.agent.package_mirrors`).
    #
    # The switch. Off, no address is probed and no room is handed one; a room
    # already carrying one is told to drop it, which is what the reset list in
    # `package_mirrors.governed` is for.
    package_mirror_enabled: bool = True
    # The index pip and uv fetch from first, and the official one behind it.
    package_mirror_pypi_index: str = "https://pypi.tuna.tsinghua.edu.cn/simple"
    package_mirror_pypi_fallback: str = "https://pypi.org/simple"
    # npm and pnpm's registry, and `playwright install`'s browser host.
    package_mirror_npm_registry: str = "https://registry.npmmirror.com"
    package_mirror_playwright_host: str = (
        "https://cdn.npmmirror.com/binaries/playwright"
    )
    # uv's standalone CPython builds. Empty by default: those are GitHub release
    # assets, and the mirrors carrying them are third-party proxies rather than
    # a distribution's own archive.
    package_mirror_uv_python: str = ""
