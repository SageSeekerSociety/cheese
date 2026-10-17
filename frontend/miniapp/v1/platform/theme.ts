// `@platform/theme`: the document's colours for what an app draws itself in
// script — on a <canvas>, say — where `var(--chart-1)` cannot be written.
// `color()` read inside a `watchEffect` or `computed` runs again when the
// reader switches between light and dark. It is shell 1's contract: it may
// grow, never change.
import { readonly, ref } from 'vue'

import { onTheme, token } from '../theme'

const current = ref<'light' | 'dark'>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
onTheme(() => {
  current.value = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
})

/** `'light'` or `'dark'`, as the document is now. */
export const theme = readonly(current)

/** A colour of the document by the name the guide gives it (`'chart-1'`,
 *  `'line'`, `'ok-ink'`, …) or by tone (`'1'` to `'8'`, `'ok'`, `'muted'`, …). */
export function color(name: string | number): string {
  void current.value
  const n = String(name).replace(/^--/, '')
  return token(/^[1-8]$/.test(n) ? `chart-${n}` : n)
}
