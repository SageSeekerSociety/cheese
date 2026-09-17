/**
 * Recover a tab that was open across a deploy.
 *
 * Every build names its chunks by content hash, and the container serving them
 * only ever holds one build — so the moment a release lands, a chunk the open
 * tab is about to lazy-load returns 404 and the click that needed it does
 * nothing at all. Deploying without downtime does not help here and never will:
 * the server is up and answering correctly about a file that no longer exists.
 * (Observed on 2026-09-15 and 09-16 as `Failed to fetch dynamically imported
 * module: .../assets/TopicView-DngyVzs7.js` — that file is gone; the running
 * build ships TopicView-DBolMOEy.js.)
 *
 * Reloading is the entire fix, because index.html is NOT content-hashed: the
 * new one names the new chunks.
 */

// Set before the reload and cleared once the app mounts again, so a failure
// that survives a reload — a chunk genuinely missing from the current build,
// an offline tab — stops after one attempt instead of looping.
const RELOADED_KEY = 'cheese:stale-build-reloaded'

// The three shapes a missing chunk arrives in: the dynamic import itself, the
// module script the browser was told to preload, and a stylesheet the same
// helper preloads.
const MISSING_CHUNK =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i

function describes(reason: unknown): string {
  if (reason instanceof Error) return `${reason.name}: ${reason.message}`
  return String(reason ?? '')
}

/** Reload once when `reason` is a chunk this build no longer serves. */
export function reloadForNewBuild(reason: unknown): boolean {
  if (!MISSING_CHUNK.test(describes(reason))) return false
  try {
    if (sessionStorage.getItem(RELOADED_KEY)) return false
    sessionStorage.setItem(RELOADED_KEY, '1')
  } catch {
    // Private windows and blocked site data throw on both calls. Reloading
    // without the guard beats leaving the tab dead: looping needs the failure
    // to outlive the reload, and a deploy's does not.
  }
  window.location.reload()
  return true
}

/** Called once the app has mounted, which proves the reload worked. */
export function clearStaleBuildGuard(): void {
  try {
    sessionStorage.removeItem(RELOADED_KEY)
  } catch {
    // Same stores that refuse the write above; nothing was written either.
  }
}

/**
 * Vite dispatches a cancelable `vite:preloadError` carrying the failure on
 * `payload`, and rethrows it unless the event is cancelled — so handling it
 * means cancelling it too, or the reload races an unhandled rejection.
 */
export function watchForStaleBuild(): void {
  window.addEventListener('vite:preloadError', (event) => {
    const reason = (event as Event & { payload?: unknown }).payload
    if (reloadForNewBuild(reason)) event.preventDefault()
  })
}
