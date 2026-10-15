"""The mirror switches, in a module of their own.

`config.py` is at the size cap every module is held to
(`.claude/scripts/check-file-sizes.py`, `.claude/rules/architecture.md`), and
this section stands alone: the addresses of the domestic mirrors a room may
fetch packages from, and the switch that turns them off. `Settings` subclasses
the class below, so the fields, their environment names and
`settings.package_mirror_*` are exactly what `config.py` declared before the
move — `backend/.env.example` is held to `Settings.model_fields`, and a field
declared anywhere on that class is a field it reads.

What the addresses mean — which mirror answers, when a mirror that does not is
left unset, and what a room is told to drop — is
`app.domain.agent.package_mirrors`, applied as process environment. None of it
reaches an agent's prompt, its skills or its memory.
"""

from pydantic import BaseModel


class PackageMirrorSettings(BaseModel):
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
