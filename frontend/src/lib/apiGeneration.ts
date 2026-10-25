/**
 * A page left open across a deploy that broke the API it was built for loads
 * the current build, instead of sending the backend a shape it no longer takes.
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
 * On that refusal the page reloads by itself, the way staleBuild.ts does: the
 * waiting service worker takes over first, or the reload could come back with
 * the old index.html. Two cases ask the person instead, with a refresh button
 * that stays until used:
 * - Messages not yet stored. The outbox is deliberately kept off disk
 *   (lib/composerDrafts.ts), so a reload drops them; the person decides when.
 *   Typed drafts are on disk and survive the reload, and a pane with unsaved
 *   edits guards the reload with the browser's own `beforeunload` question.
 * - A reload from this same generation already happened in this tab. The page
 *   that came back is still outdated (the new frontend is not served yet, or
 *   a cache answered), and reloading again would loop. Where the tab cannot
 *   remember that (blocked storage), it never reloads by itself, for the same
 *   reason.
 */
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { holdsUnsentMessages } from '@/lib/composerDrafts'
import { takeWaitingWorker } from '@/pwa'

/** The generation this build speaks; absent in builds of the other vite configs, which then say nothing. */
const GENERATION = import.meta.env.VITE_API_GENERATION

/** The refusal's `error.name`, and its WebSocket close code. */
export const CLIENT_OUTDATED = 'ClientOutdated'
export const CLIENT_OUTDATED_CLOSE = 4409

const RELOADED_KEY = 'cheese:api-generation-reloaded'

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

let handled = false

/** The backend said this page is outdated: load the current build, or ask first (see the file header). */
export function reloadOutdatedPage(): void {
  if (handled) return
  handled = true
  const reloadedAlready = storageGet(RELOADED_KEY, 'session') === GENERATION
  if (holdsUnsentMessages() || reloadedAlready || !storageSet(RELOADED_KEY, GENERATION ?? '', 'session')) {
    toast.warning(t('global.outdatedClient.message'), {
      duration: Number.POSITIVE_INFINITY,
      action: { label: t('global.outdatedClient.refresh'), onClick: reload },
    })
    return
  }
  reload()
}
