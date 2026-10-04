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
 * Reloading is the fix, because index.html is NOT content-hashed: the new one
 * names the new chunks. But the reload has to reach the new index.html, and
 * while the previous service worker still controls the tab it can come back
 * with the old one: that worker's navigation route falls back to its own
 * cached index.html whenever the network fetch fails, and a tab open across
 * the 2026-09-30 releases did reload onto the old build and could not open a
 * topic. So the new worker takes over first (`takeWaitingWorker`), then the
 * tab reloads.
 *
 * Two callers handle a missing chunk themselves, and Vite still announces
 * their failures as `vite:preloadError`, so the global listener must stay out
 * of their way:
 * - a prefetch (lib/routePrefetch.ts) failing means nothing was prefetched.
 *   Reloading for it reloaded the tab under a hovering pointer, and on a
 *   pointerdown it cancelled the click's own navigation and reloaded the page
 *   the click was leaving.
 * - a navigation fails through `router.onError`, which knows where the click
 *   was going (`recoverNavigations`). The global listener would reload the
 *   current page instead, and its one-shot guard would then stop the router
 *   from loading the target.
 *
 * A weak network fails a chunk the same way a deploy does, and in WebKit (iOS
 * Safari, and the WeChat in-app browser on iPhone) a module that failed to
 * load stays failed for the life of the page: importing it again rejects at
 * once without asking the server. So nothing short of a full load opens that
 * page again, and when one full load did not help, the person is told and
 * given the retry instead of tapping a row that silently does nothing.
 */
import type { Router } from 'vue-router'

import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { prefetchingChunks } from '@/lib/routePrefetch'
import { takeWaitingWorker } from '@/pwa'

// Set before the reload and cleared once a navigation lands, so a failure
// that survives a reload — a chunk genuinely missing from the current build,
// an offline tab — stops after one attempt instead of looping. Not cleared
// when the app mounts: the first route's chunk loads after that, so a page
// whose own chunk keeps failing reloaded itself several times a second.
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

/**
 * Reload once when `reason` is a chunk this build no longer serves: onto
 * `target` when the failure belongs to a navigation, otherwise this page.
 */
export function reloadForNewBuild(reason: unknown, target?: string): boolean {
  if (!MISSING_CHUNK.test(describes(reason))) return false
  try {
    if (sessionStorage.getItem(RELOADED_KEY)) return false
    sessionStorage.setItem(RELOADED_KEY, '1')
  } catch {
    // Private windows and blocked site data throw on both calls. Reloading
    // without the guard beats leaving the tab dead: looping needs the failure
    // to outlive the reload, and a deploy's does not.
  }
  void takeWaitingWorker({ check: true }).finally(() =>
    target === undefined ? window.location.reload() : window.location.assign(target)
  )
  return true
}

// The navigation in progress, if any; its chunk failures go to `onError`.
let navigating: unknown = null
// The "page didn't open" notice on screen, put away once a page does open.
let notice: string | number | null = null

/**
 * A navigation whose route chunk is gone loads the page it was going to.
 * Claimed from a beforeEach: the chunks load while the route's components
 * resolve, which is after every beforeEach.
 */
export function recoverNavigations(router: Router): void {
  router.beforeEach((to) => {
    navigating = to
  })
  const settle = (to: unknown) => {
    if (navigating === to) navigating = null
  }
  router.afterEach((to, _from, failure) => {
    settle(to)
    if (failure) return
    // A page opened, so its chunks load: the next missing one gets its own full load.
    clearStaleBuildGuard()
    if (notice !== null) toast.dismiss(notice)
    notice = null
  })
  router.onError((error, to) => {
    settle(to)
    const target = router.resolve(to).href
    if (reloadForNewBuild(error, target) || !MISSING_CHUNK.test(describes(error))) return
    if (notice !== null) toast.dismiss(notice)
    notice = toast.warning(t('global.loadError.page'), {
      // The page it was going to is not on screen and will not be; the retry stays until used.
      duration: Number.POSITIVE_INFINITY,
      action: {
        label: t('global.loadError.retry'),
        onClick: () => {
          clearStaleBuildGuard()
          reloadForNewBuild(error, target)
        },
      },
    })
  })
}

function clearStaleBuildGuard(): void {
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
    if (navigating !== null || prefetchingChunks()) return
    const reason = (event as Event & { payload?: unknown }).payload
    if (reloadForNewBuild(reason)) event.preventDefault()
  })
}
