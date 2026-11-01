/**
 * A page left open across a deploy that broke the API it was built for is told
 * to load the current build, instead of sending the backend a shape it no
 * longer takes.
 *
 * pwa.ts swaps builds on the next navigation, so a page that stays on one room
 * or task for hours keeps the old code until then; while the backend's API is
 * unchanged that costs nothing. A change that renames a field or reshapes a
 * request lands in both halves at once (AGENTS.md keeps no compatibility path),
 * and raises the API generation, one number in `backend/api-generation` that
 * this build bakes in (vite.config.ts). Every request says which generation it
 * was built for; the backend refuses one below its own as `ClientOutdated`
 * (409, or close code 4409 on a WebSocket: backend/app/core/api_generation.py).
 *
 * On that refusal the page asks for a refresh, with a button that stays until
 * used, rather than reloading by itself: what a person has typed is not all on
 * disk. Chat drafts are (lib/composerDrafts.ts), but the outbox is deliberately
 * not, and neither are the task form, a review comment or an agent's settings,
 * and a reload would drop them without a word. The button reloads the way
 * staleBuild.ts does: the waiting service worker takes over first, or the
 * reload could come back with the old index.html.
 *
 * That notice is then the only failure the page reports: every request it sends
 * after the refusal is refused the same way, and each caller's own failure
 * notice would stack another account of it (lib/toast.ts holds them). So it is
 * shown with vuetify-sonner's own `toast`, the one notice the hold lets through.
 */
import { watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { firstPaintDone } from '@/lib/afterFirstPaint'
import { holdFailureNotices } from '@/lib/toast'
import { takeWaitingWorker } from '@/pwa'

/** The generation this build speaks; absent in builds of the other vite configs, which then say nothing. */
const GENERATION = import.meta.env.VITE_API_GENERATION

/** The refusal's `error.name`, and its WebSocket close code. */
export const CLIENT_OUTDATED = 'ClientOutdated'
export const CLIENT_OUTDATED_CLOSE = 4409

/** Headers that tell the backend which API generation this page speaks. */
export function apiGenerationHeaders(): Record<string, string> {
  return GENERATION ? { 'X-Cheese-Api-Gen': GENERATION } : {}
}

/** The same, for a WebSocket URL, which a browser cannot give headers: `api_gen=N`, or ''. */
export function apiGenerationQuery(): string {
  return GENERATION ? `api_gen=${encodeURIComponent(GENERATION)}` : ''
}

function reload(): void {
  void takeWaitingWorker({ check: true }).finally(() => window.location.reload())
}

function show(): void {
  toast.warning(t('global.outdatedClient.message'), {
    // The page cannot be used until it is refreshed; the button stays until it is.
    duration: Number.POSITIVE_INFINITY,
    action: { label: t('global.outdatedClient.refresh'), onClick: reload },
  })
}

let asked = false

/**
 * The backend said this page is outdated: ask for a refresh, once. A page
 * refused on its first requests is refused before the shell that holds the
 * notices (AppShell) is on screen, and a notice raised then is never shown, so
 * it waits for the first screen.
 */
export function askToReload(): void {
  if (asked) return
  asked = true
  // Held now, not when the notice shows: the callers whose requests were just
  // refused raise their own failure notices as soon as this returns.
  holdFailureNotices()
  if (firstPaintDone.value) {
    show()
    return
  }
  const stop = watch(firstPaintDone, (done) => {
    if (!done) return
    stop()
    show()
  })
}
