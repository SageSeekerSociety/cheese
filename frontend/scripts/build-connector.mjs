// Builds every client artifact `pnpm build` ships and drops it under
// public/connector/latest/<os>-<arch>/, where vite then copies it into dist/ so it
// is served from the same web origin as the app (see CLAUDE.md: binaries live at
// <origin>/connector/latest/...). Two artifacts:
//   * `cheese`  — the connector CLI, cross-compiled with the Go toolchain for
//                 linux/darwin × amd64/arm64;
//   * `tmux`    — a fully static tmux the client hosts on the target, built via
//                 frontend/scripts/tmux (Docker + musl). Static tmux is Linux-only,
//                 so only the linux-* dirs get one; macOS uses its own tmux.
//
// The dir name is keyed by `uname -s | lower`-`uname -m` so the install script can
// resolve its own platform with one line. There is deliberately no Windows target.
//
// Best-effort throughout: if Go is absent this warns and exits 0 so the web app
// still builds; if Docker is absent (or a tmux target fails) it skips just that
// artifact. The tmux build is cached in .connector-dist and only recompiled when
// missing (or CONNECTOR_REBUILD_TMUX=1), so repeat `pnpm build`s stay fast — the
// cached binary is just copied into public/.

import { execFileSync } from 'node:child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(here, '..')
const repoRoot = resolve(frontendDir, '..')
const cheesedDir = resolve(repoRoot, 'cli')
const outRoot = resolve(frontendDir, 'public/connector/latest')

// GOOS/GOARCH -> the `uname -s|lower`-`uname -m` directory the installer looks in.
const TARGETS = [
  { goos: 'linux', goarch: 'amd64', dir: 'linux-x86_64' },
  { goos: 'linux', goarch: 'arm64', dir: 'linux-aarch64' },
  { goos: 'darwin', goarch: 'amd64', dir: 'darwin-x86_64' },
  { goos: 'darwin', goarch: 'arm64', dir: 'darwin-arm64' },
]

function findGo() {
  for (const candidate of ['go', '/usr/local/bin/go', '/usr/local/go/bin/go']) {
    try {
      execFileSync(candidate, ['version'], { stdio: 'ignore' })
      return candidate
    } catch {
      /* try next */
    }
  }
  return null
}

const go = findGo()
if (!go) {
  console.warn('[build-connector] Go toolchain not found — skipping connector binaries.')
  console.warn('[build-connector] The web app will build; install Go to ship cheesed downloads.')
  process.exit(0)
}

rmSync(outRoot, { recursive: true, force: true })

let ok = 0
for (const { goos, goarch, dir } of TARGETS) {
  const outDir = resolve(outRoot, dir)
  mkdirSync(outDir, { recursive: true })
  const out = resolve(outDir, 'cheese')
  try {
    execFileSync(go, ['build', '-trimpath', '-ldflags=-s -w', '-o', out, '.'], {
      cwd: cheesedDir,
      env: { ...process.env, CGO_ENABLED: '0', GOOS: goos, GOARCH: goarch },
      stdio: 'inherit',
    })
    console.log(`[build-connector] built ${dir}/cheese`)
    ok++
  } catch (err) {
    console.error(`[build-connector] FAILED ${goos}/${goarch}:`, err.message)
    process.exit(1)
  }
}
console.log(`[build-connector] ${ok}/${TARGETS.length} cheese binaries → public/connector/latest/`)

// --- static tmux (Linux only) --------------------------------------------------
// Built with Docker + musl by frontend/scripts/tmux, cached in .connector-dist so
// only the first build compiles it; every build then copies the cache into public/.
const TMUX_TARGETS = [
  { arch: 'amd64', dir: 'linux-x86_64' },
  { arch: 'arm64', dir: 'linux-aarch64' },
]
const tmuxScript = resolve(here, 'tmux/build-static-tmux.sh')
const tmuxCache = resolve(repoRoot, '.connector-dist')

function hasDocker() {
  try {
    execFileSync('docker', ['version'], { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

if (!hasDocker()) {
  console.warn('[build-connector] Docker not found — skipping static tmux (Linux clients).')
  console.warn('[build-connector] The web app + cheese binaries still ship; install Docker for tmux.')
} else {
  let tmuxOk = 0
  for (const { arch, dir } of TMUX_TARGETS) {
    const cached = resolve(tmuxCache, `linux-${arch}`, 'tmux')
    if (!existsSync(cached) || process.env.CONNECTOR_REBUILD_TMUX) {
      try {
        execFileSync('bash', [tmuxScript, '--arch', arch, '--out', tmuxCache], { stdio: 'inherit' })
      } catch (err) {
        // arm64 needs buildx + qemu; a missing emulator shouldn't fail the web build.
        console.warn(`[build-connector] tmux ${arch} build failed — skipping:`, err.message)
        continue
      }
    }
    if (existsSync(cached)) {
      const dest = resolve(outRoot, dir, 'tmux')
      mkdirSync(dirname(dest), { recursive: true })
      copyFileSync(cached, dest)
      chmodSync(dest, 0o755)
      console.log(`[build-connector] tmux → ${dir}/tmux`)
      tmuxOk++
    }
  }
  console.log(`[build-connector] ${tmuxOk}/${TMUX_TARGETS.length} tmux binaries → public/connector/latest/`)
}
