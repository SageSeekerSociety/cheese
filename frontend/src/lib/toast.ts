/**
 * The page's notices: vuetify-sonner's `toast`, which every caller imports from
 * here (eslint keeps it that way), so that what holds for every notice holds in
 * one place.
 *
 * What holds: once the backend has refused this page as outdated
 * (lib/apiGeneration.ts), the request for a refresh is the only failure notice
 * the page shows. Every request the page sends from then on is refused for that
 * same reason, so each caller's own failure notice — "the project list did not
 * refresh", "saving failed", a server sentence repeated — would only stack a
 * second, misleading account of the one thing the refresh notice already says.
 * `error` and `warning` are held from that moment; the other kinds (a success,
 * an info) are not failures and still show.
 */
import { readonly, ref } from 'vue'
import { toast as sonner } from 'vuetify-sonner'

const held = ref(false)

/** True once failure notices are held: the page knows it is outdated. Notices
 *  drawn outside `toast` (the chat's own error strip) read this. */
export const failureNoticesHeld = readonly(held)

/** The page is outdated: from now on its failure notices are not shown. */
export function holdFailureNotices(): void {
  held.value = true
}

let heldCount = 0

function unlessHeld(kind: 'error' | 'warning'): typeof sonner.error {
  return (...args) => {
    // A caller may keep the id to dismiss its notice later; an id no toast has
    // makes that dismissal a no-op, where an empty one would dismiss them all,
    // the refresh notice included.
    if (held.value) return `held-${++heldCount}`
    return sonner[kind](...args)
  }
}

const gated = { error: unlessHeld('error'), warning: unlessHeld('warning') }

// A proxy rather than a copy, so every other method is vuetify-sonner's own at
// the moment it is called.
export const toast: typeof sonner = new Proxy(sonner, {
  get: (target, key, receiver) =>
    key === 'error' || key === 'warning' ? gated[key] : Reflect.get(target, key, receiver),
})
